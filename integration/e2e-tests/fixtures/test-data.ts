// Shared test data + helpers. Keep secrets in .env, not here.

export const TEST_ADDRESS =
  process.env.TEST_ADDRESS || "624 Merrill Ave, Bedford, OH";

// Address that should NOT resolve (used for negative tests).
// Garbage that no geocoder will accept.
export const BAD_ADDRESS = "zzzzzz no such place qqqqqq";

export const ADMIN_EMAIL =
  process.env.ADMIN_EMAIL || "admin@roofmeasure.local";

export const ADMIN_PASSWORD =
  process.env.ADMIN_PASSWORD || "Admin@12345";

// Standard PDF magic number. Used to verify the downloaded file is real.
export const PDF_MAGIC = Buffer.from([0x25, 0x50, 0x44, 0x46]); // "%PDF"

export function requireEnv(name: string): string {
  const v = process.env[name];
  if (!v) throw new Error(`missing env var ${name}`);
  return v;
}
