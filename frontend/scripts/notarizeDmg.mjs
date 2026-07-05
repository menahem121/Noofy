import { existsSync, readdirSync, statSync } from "node:fs";
import path from "node:path";
import process from "node:process";

import { frontendRoot, relative, runChecked } from "./packagedRuntime.mjs";

const args = parseArgs(process.argv.slice(2));
const dmgPath = path.resolve(args.dmg || newestDmgPath());

try {
  if (process.platform !== "darwin") {
    throw new Error("DMG notarization must run on macOS.");
  }
  if (!existsSync(dmgPath)) {
    throw new Error(`DMG does not exist: ${relative(dmgPath)}`);
  }

  const submitArgs = ["notarytool", "submit", dmgPath, "--wait", ...notaryAuthArgs()];
  runChecked("xcrun", submitArgs);
  runChecked("xcrun", ["stapler", "staple", dmgPath]);
  runChecked("xcrun", ["stapler", "validate", dmgPath]);
  runChecked("spctl", [
    "-a",
    "-vvv",
    "-t",
    "open",
    "--context",
    "context:primary-signature",
    dmgPath,
  ]);

  console.log(`Notarized and stapled ${relative(dmgPath)}.`);
} catch (error) {
  console.error(error instanceof Error ? error.message : String(error));
  process.exit(1);
}

function notaryAuthArgs() {
  const profile = process.env.APPLE_NOTARYTOOL_PROFILE || process.env.APPLE_KEYCHAIN_PROFILE;
  if (profile) {
    return ["--keychain-profile", profile];
  }

  const apiKey = process.env.APPLE_API_KEY;
  const apiIssuer = process.env.APPLE_API_ISSUER;
  const apiKeyPath = process.env.APPLE_API_KEY_PATH;
  if (apiKey && apiIssuer && apiKeyPath) {
    return ["--key", apiKeyPath, "--key-id", apiKey, "--issuer", apiIssuer];
  }

  const appleId = process.env.APPLE_ID;
  const password = process.env.APPLE_PASSWORD;
  const teamId = process.env.APPLE_TEAM_ID;
  if (appleId && password && teamId) {
    return ["--apple-id", appleId, "--password", password, "--team-id", teamId];
  }

  throw new Error(
    [
      "Missing notarization credentials.",
      "Set APPLE_ID, APPLE_PASSWORD, and APPLE_TEAM_ID, or use APPLE_NOTARYTOOL_PROFILE.",
    ].join("\n"),
  );
}

function newestDmgPath() {
  const dmgDir = path.join(frontendRoot, "src-tauri", "target", "release", "bundle", "dmg");
  if (!existsSync(dmgDir)) {
    throw new Error(`DMG output directory does not exist: ${relative(dmgDir)}`);
  }
  const dmgs = readdirSync(dmgDir)
    .filter((entry) => entry.endsWith(".dmg"))
    .map((entry) => path.join(dmgDir, entry))
    .sort((left, right) => statSync(right).mtimeMs - statSync(left).mtimeMs);
  if (dmgs.length === 0) {
    throw new Error(`No DMG files found under ${relative(dmgDir)}.`);
  }
  return dmgs[0];
}

function parseArgs(argv) {
  const parsed = {};
  for (let index = 0; index < argv.length; index += 1) {
    const key = argv[index];
    if (key !== "--dmg") {
      throw new Error(`Unexpected argument: ${key}`);
    }
    const value = argv[index + 1];
    if (!value || value.startsWith("--")) {
      throw new Error(`Missing value for ${key}`);
    }
    parsed.dmg = value;
    index += 1;
  }
  return parsed;
}
