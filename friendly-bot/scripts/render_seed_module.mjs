import { pathToFileURL } from "node:url";

const [seedPath] = process.argv.slice(2);

if (process.argv.length !== 3) {
  console.error("expected one TypeScript seed module path");
  process.exitCode = 1;
} else {
  try {
    const { default: seed } = await import(pathToFileURL(seedPath).href);
    if (seed === null || Array.isArray(seed) || typeof seed !== "object") {
      throw new TypeError("seed module must default-export an object");
    }
    process.stdout.write(JSON.stringify(seed));
  } catch {
    console.error("could not render TypeScript seed module");
    process.exitCode = 1;
  }
}
