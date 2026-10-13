import { spawnSync } from "node:child_process";

const root = process.cwd();
const listed = spawnSync("npx", ["playwright", "test", "--list"], {
  cwd: root,
  encoding: "utf8",
});
if (listed.status !== 0) process.exit(listed.status ?? 1);
const tests = listed.stdout
  .split("\n")
  .map((line) => line.trim())
  .filter((line) => line && !line.startsWith("Listing tests:") && !line.startsWith("Total:"))
  .map((line) => line.split(" › ", 1)[0]);
// Deterministic shuffle: the order is randomised for the suite, and the seed
// is printed so a failure can be reproduced exactly.
const seed = Number(process.env.E2E_RANDOM_SEED ?? 20261013);
let state = seed >>> 0;
const random = () => {
  state = (1664525 * state + 1013904223) >>> 0;
  return state / 0x100000000;
};
const files = [...new Set(tests.map((test) => test.split(":", 1)[0]))];
for (let i = files.length - 1; i > 0; i -= 1) {
  const j = Math.floor(random() * (i + 1));
  [files[i], files[j]] = [files[j], files[i]];
}
console.log(`random Playwright file order: seed=${seed}, tests=${tests.length}`);
console.log(files.join(" → "));
for (const file of files) {
  const result = spawnSync("npx", ["playwright", "test", file], {
    cwd: root,
    stdio: "inherit",
    env: process.env,
  });
  if (result.status !== 0) process.exit(result.status ?? 1);
}
process.exit(0);
