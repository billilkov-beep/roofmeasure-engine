"""Microsoft Global ML Buildings via Planetary Computer STAC."""
import logging, math
from typing import Optional
from shapely.geometry import Point, box

LOG = logging.getLogger("roofmeasure.ms_global_ml")


def get_global_ml_polygon(lat: float, lon: float, search_radius_m: float = 200):
    try:
        import pystac_client
        import planetary_computer as pc
        import geopandas as gpd

        dlat = search_radius_m / 111000.0
        dlon = search_radius_m / (111000.0 * max(0.1, abs(math.cos(math.radians(lat)))))
        bbox = [lon - dlon, lat - dlat, lon + dlon, lat + dlat]

        catalog = pystac_client.Client.open(
            "https://planetarycomputer.microsoft.com/api/stac/v1",
            modifier=pc.sign_inplace,
        )
        items = list(catalog.search(collections=["ms-buildings"], bbox=bbox, limit=5).items())
        if not items:
            LOG.info(f"STAC no items near ({lat:.5f},{lon:.5f})")
            return None

        target = Point(lon, lat)
        search_box = box(*bbox)
        best = None
        best_dist = float("inf")

        for it in items:
            try:
                asset = it.assets["data"]
                storage_options = asset.extra_fields.get("table:storage_options", {})
                gdf = gpd.read_parquet(asset.href, storage_options=storage_options)
                if gdf.empty:
                    continue
                in_box = gdf[gdf.geometry.intersects(search_box)]
                if in_box.empty:
                    continue
                for _, row in in_box.iterrows():
                    poly = row.geometry
                    if poly is None: continue
                    if poly.contains(target):
                        LOG.info(f"global_ml: contained match")
                        return poly
                    d = poly.distance(target)
                    if d < best_dist:
                        best = poly; best_dist = d
            except Exception as e:
                LOG.warning(f"parquet read err: {e}")
                continue

        if best is not None:
            LOG.info(f"global_ml: nearest match dist={best_dist:.6f}deg")
        return best
    except Exception as e:
        LOG.warning(f"global_ml outer err: {e}")
        return None
