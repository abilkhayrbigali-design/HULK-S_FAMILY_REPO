import { readFileSync } from "node:fs";

export const fixture = (name: string) =>
  JSON.parse(
    readFileSync(
      new URL(`../../data/fixtures/synthetic/${name}`, import.meta.url),
      "utf8",
    ),
  );
