import process from "node:process";

import { relative, verifyPackagedRuntime } from "./packagedRuntime.mjs";

try {
  const result = verifyPackagedRuntime();
  if (result.skipped) {
    console.warn(result.message);
  } else {
    console.log(
      [
        `Packaged runtime verified for ${result.target}.`,
        `Python: ${relative(result.python)} (${result.manifest.python.version}, ${result.manifest.python.buildId})`,
        `uv: ${relative(result.uv)} (${result.manifest.uv.version})`,
      ].join("\n"),
    );
  }
} catch (error) {
  console.error(error instanceof Error ? error.message : String(error));
  process.exit(1);
}
