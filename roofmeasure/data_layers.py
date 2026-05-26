"""Google Solar API dataLayers fetcher + per-facet polygon extraction.""" 
# AUTO_DEPLOY_TEST_v2_works_from_local
from __future__ import annotations

import io
import logging
import math
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

import numpy as np
import requests
import tifffile
from pyproj import Transformer
from scipy.ndimage import binary_closing, binary_opening, label as cc_label, median_filter
from skimage import measure

logger = logging.getLogger(__name__)

DATA_LAYERS_URL = "https://solar.googleapis.com/v1/dataLayers:get"


@dataclass
class GeoTiff:
    array: np.ndarray
    affine: tuple
    epsg: int
    _transformer: Any = field(default=None, init=False)
    _inv_transformer: Any = field(default=None, init=False)

    def __post_init__(self):
        if self.epsg != 4326:
            self._transformer = Transformer.from_crs(f"EPSG:{self.epsg}", "EPSG:4326", always_xy=True)
            self._inv_transformer = Transformer.from_crs("EPSG:4326", f"EPSG:{self.epsg}", always_xy=True)

    def pixel_to_world(self, px, py):
        a, b, c, d, e, f = self.affine
        return a * px + b * py + c, d * px + e * py + f

    def world_to_pixel(self, wx, wy):
        a, b, c, d, e, f = self.affine
        px = (wx - c) / a if a != 0 else 0
        py = (wy - f) / e if e != 0 else 0
        return px, py

    def latlng_to_pixel(self, lat, lng):
        if self._inv_transformer is None:
            wx, wy = lng, lat
        else:
            wx, wy = self._inv_transformer.transform(lng, lat)
        return self.world_to_pixel(wx, wy)

    def pixels_to_latlng_batch(self, px_arr, py_arr):
        a, b, c, d, e, f = self.affine
        wx = a * px_arr + b * py_arr + c
        wy = d * px_arr + e * py_arr + f
        if self._transformer is None:
            return wy, wx
        lng, lat = self._transformer.transform(wx, wy)
        return lat, lng

    def pixel_size_meters(self, center_lat=None):
        a, _, _, _, e, _ = self.affine
        if self.epsg == 4326:
            m_per_deg_lng = 111320.0 * math.cos(math.radians(center_lat))
            return abs(a) * m_per_deg_lng, abs(e) * 111320.0
        return abs(a), abs(e)


def fetch_meta(lat, lng, api_key, radius_m=30):
    params = {"location.latitude": lat, "location.longitude": lng, "radiusMeters": radius_m,
              "view": "FULL_LAYERS", "requiredQuality": "HIGH", "key": api_key}
    resp = requests.get(f"{DATA_LAYERS_URL}?{urlencode(params)}", timeout=30)
    resp.raise_for_status()
    return resp.json()


def fetch_geotiff(url, api_key, timeout=60):
    signed = url + (f"&key={api_key}" if "key=" not in url else "")
    resp = requests.get(signed, timeout=timeout)
    resp.raise_for_status()
    return parse_geotiff(resp.content)


def _affine_from_tags(page):
    trans = page.tags.get("ModelTransformationTag") or page.tags.get(34264)
    if trans:
        m = trans.value
        return (m[0], m[1], m[3], m[4], m[5], m[7])
    tp = page.tags.get("ModelTiepointTag") or page.tags.get(33922)
    sc = page.tags.get("ModelPixelScaleTag") or page.tags.get(33550)
    if tp and sc:
        ti, tj, _, tx, ty, _ = tp.value[:6]
        sx, sy, _ = sc.value[:3]
        return (sx, 0.0, tx - ti * sx, 0.0, -sy, ty + tj * sy)
    raise ValueError("GeoTIFF missing geo tags")


def _epsg_from_geokeys(page):
    gkd = page.tags.get("GeoKeyDirectoryTag") or page.tags.get(34735)
    if not gkd:
        return 4326
    keys = gkd.value
    i = 4
    while i + 3 < len(keys):
        key_id, _, _, value = keys[i], keys[i+1], keys[i+2], keys[i+3]
        if key_id in (3072, 2048):
            return int(value)
        i += 4
    return 4326


def parse_geotiff(data):
    with tifffile.TiffFile(io.BytesIO(data)) as tif:
        page = tif.pages[0]
        return GeoTiff(array=page.asarray(), affine=_affine_from_tags(page), epsg=_epsg_from_geokeys(page))


def compute_normals(dsm, dx_m, dy_m):
    dsm_f = dsm.astype(np.float32)
    gy, gx = np.gradient(dsm_f)
    nx = -gx / dx_m
    ny = gy / dy_m
    nz = np.ones_like(gx)
    norm = np.sqrt(nx * nx + ny * ny + nz * nz)
    norm[norm == 0] = 1.0
    return np.stack([nx / norm, ny / norm, nz / norm], axis=-1)


def facet_normal(pitch_deg, azimuth_deg):
    p = math.radians(pitch_deg)
    a = math.radians(azimuth_deg)
    return np.array([math.sin(a) * math.sin(p), math.cos(a) * math.sin(p), math.cos(p)], dtype=np.float32)


def isolate_target_building(mask):
    labeled = measure.label(mask > 0, connectivity=2)
    cy, cx = mask.shape[0] // 2, mask.shape[1] // 2
    target = int(labeled[cy, cx])
    if target == 0:
        roof_yx = np.argwhere(labeled > 0)
        if roof_yx.size == 0:
            return mask
        d = (roof_yx[:, 0] - cy) ** 2 + (roof_yx[:, 1] - cx) ** 2
        ny, nx = roof_yx[d.argmin()]
        target = int(labeled[ny, nx])
    return (labeled == target).astype(np.uint8)


def _group_similar_facets(facets, tol_deg=15.0):
    fn = np.array(
        [facet_normal(f.get("pitchDeg", 0), f.get("azimuthDeg", 0)) for f in facets],
        dtype=np.float32,
    )
    tol_cos = math.cos(math.radians(tol_deg))
    n = len(facets)
    assigned = [False] * n
    groups = []
    for i in range(n):
        if assigned[i]:
            continue
        members = {i}
        assigned[i] = True
        for j in range(i + 1, n):
            if assigned[j]:
                continue
            if float(np.dot(fn[i], fn[j])) >= tol_cos:
                members.add(j)
                assigned[j] = True
        rep = max(members, key=lambda k: facets[k].get("areaSqFt", 0))
        groups.append((rep, members))
    return groups


def assign_pixels(normals, mask, facets, geo, merge_tol_deg=15.0):
    H, W = mask.shape
    groups = _group_similar_facets(facets, tol_deg=merge_tol_deg)
    reps = [g[0] for g in groups]
    rep_normals = np.array(
        [facet_normal(facets[r].get("pitchDeg", 0), facets[r].get("azimuthDeg", 0)) for r in reps],
        dtype=np.float32,
    )
    rep_centers_px = []
    for r in reps:
        f = facets[r]
        if f.get("centerLat") is None or f.get("centerLon") is None:
            rep_centers_px.append((W / 2, H / 2))
        else:
            cx, cy = geo.latlng_to_pixel(f["centerLat"], f["centerLon"])
            rep_centers_px.append((cx, cy))
    centers_arr = np.array(rep_centers_px, dtype=np.float32)
    yy, xx = np.mgrid[0:H, 0:W]
    diag = math.sqrt(H * H + W * W)
    norm_sim = normals @ rep_normals.T
    dx = xx[..., None] - centers_arr[:, 0][None, None, :]
    dy = yy[..., None] - centers_arr[:, 1][None, None, :]
    sp_dist = np.sqrt(dx * dx + dy * dy)
    sp_score = 1.0 - sp_dist / diag
    score = 0.70 * norm_sim + 0.30 * sp_score
    best_group = score.argmax(axis=-1).astype(np.int16)
    best_group[mask == 0] = -1
    raw = np.full_like(best_group, -1)
    for gid, rep in enumerate(reps):
        raw[best_group == gid] = rep
    smoothed = median_filter(raw.astype(np.int16), size=5)
    smoothed[mask == 0] = -1
    cleaned = np.full_like(smoothed, -1)
    for rep in reps:
        binary = (smoothed == rep).astype(np.uint8)
        if binary.sum() == 0:
            continue
        labeled, n_cc = cc_label(binary)
        if n_cc <= 1:
            cleaned[binary > 0] = rep
            continue
        sizes = np.bincount(labeled.flatten())
        sizes[0] = 0
        largest_label = int(sizes.argmax())
        cleaned[labeled == largest_label] = rep
    logger.info("data_layers: merged %d facets into %d polygon groups", len(facets), len(groups))
    return cleaned, groups


def _polygon_area(contour):
    x = contour[:, 1]
    y = contour[:, 0]
    return 0.5 * abs(np.dot(x, np.roll(y, 1)) - np.dot(y, np.roll(x, 1)))


def extract_polygons(assignment, facets, geo, min_pixels=20, simplify_tolerance=8.0):
    out = {}
    for fid in range(len(facets)):
        m = (assignment == fid).astype(np.uint8)
        if m.sum() < min_pixels:
            continue
        # Morphological smoothing for cleaner edges
        m = binary_closing(m, iterations=3).astype(np.uint8)
        m = binary_opening(m, iterations=1).astype(np.uint8)
        if m.sum() < min_pixels:
            continue
        contours = measure.find_contours(m, 0.5)
        if not contours:
            continue
        largest = max(contours, key=_polygon_area)
        if simplify_tolerance > 0:
            largest = measure.approximate_polygon(largest, tolerance=simplify_tolerance)
        if len(largest) < 4:
            continue
        rows = np.array([p[0] for p in largest])
        cols = np.array([p[1] for p in largest])
        lats, lngs = geo.pixels_to_latlng_batch(cols, rows)
        out[fid] = [[round(float(lat), 7), round(float(lng), 7)] for lat, lng in zip(lats, lngs)]
    return out


def _resize_to(arr, shape):
    if arr.shape == shape:
        return arr
    from PIL import Image
    img = Image.fromarray(arr.astype(np.uint8)).resize((shape[1], shape[0]), Image.NEAREST)
    return np.array(img, dtype=np.uint8)


def add_polygons_to_facets(facets, lat, lng, api_key, radius_m=30):
    if not facets:
        return facets
    for f in facets:
        f.setdefault("polygon", None)
        f.setdefault("mergedIntoId", None)
    if not api_key:
        logger.warning("data_layers: no api_key")
        return facets
    try:
        meta = fetch_meta(lat, lng, api_key, radius_m=radius_m)
        mask_url, dsm_url = meta.get("maskUrl"), meta.get("dsmUrl")
        if not mask_url or not dsm_url:
            return facets
        mask_tif = fetch_geotiff(mask_url, api_key)
        dsm_tif = fetch_geotiff(dsm_url, api_key)
        logger.info("data_layers: mask shape=%s dsm shape=%s epsg=%s",
                    mask_tif.array.shape, dsm_tif.array.shape, dsm_tif.epsg)
        mask = _resize_to(mask_tif.array, dsm_tif.array.shape)
        mask = isolate_target_building(mask)
        dx_m, dy_m = dsm_tif.pixel_size_meters(center_lat=lat)
        normals = compute_normals(dsm_tif.array, dx_m, dy_m)
        assignment, groups = assign_pixels(normals, mask, facets, dsm_tif)
        polygons = extract_polygons(assignment, facets, dsm_tif)
        # Mark non-representative members of each group as merged
        for rep, members in groups:
            for m in members:
                if m != rep:
                    facets[m]["mergedIntoId"] = rep
        for fid, poly in polygons.items():
            facets[fid]["polygon"] = poly
        logger.info("data_layers: extracted %d polygons for %d facets",
                    len(polygons), len(facets))
    except Exception as e:
        logger.exception("data_layers: polygon extraction failed: %s", e)
    return facets
