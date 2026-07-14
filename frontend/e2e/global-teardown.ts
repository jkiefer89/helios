import { rm } from "node:fs/promises";

export default async function globalTeardown() {
  const temporaryDirectory = process.env.HELIOS_E2E_TEMP_DIR;
  if (!temporaryDirectory) return;
  await rm(temporaryDirectory, { force: true, recursive: true });
}
