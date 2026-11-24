import { build } from "esbuild";
import { copyFile, readdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";

const result = await build({
  entryPoints: ["src/main.jsx"],
  outfile: "../web/app.js",
  bundle: true,
  minify: true,
  jsx: "automatic",
  target: ["chrome110", "edge110"],
  define: { "process.env.NODE_ENV": '"production"' },
  legalComments: "eof",
  metafile: true,
  logLevel: "info",
});

// Include license notices only for the packages which contributed code to the release.
const packages = new Set(["tailwindcss", "tw-animate-css"]);
for (const output of Object.values(result.metafile.outputs)) {
  for (const [file, entry] of Object.entries(output.inputs)) {
    const name = file.split("node_modules/").at(-1);
    if (name !== file && entry.bytesInOutput) {
      packages.add(name.split("/").slice(0, name.startsWith("@") ? 2 : 1).join("/"));
    }
  }
}
const notices = ["shadcn/ui\n" + await readFile("src/components/ui/LICENSE", "utf8")];
for (const name of [...packages].sort()) {
  const directory = path.join("node_modules", name);
  const license = (await readdir(directory)).find(f => /^licen[cs]e(?:\.\w+)?$/i.test(f));
  const metadata = JSON.parse(await readFile(path.join(directory, "package.json"), "utf8"));
  // Some npm distributions declare the license and attribution only in their metadata.
  const notice = license ? await readFile(path.join(directory, license), "utf8")
    : [metadata.license, metadata.author, metadata.repository?.url || metadata.repository].filter(Boolean).join("\n");
  notices.push(name + " " + metadata.version + "\n" + notice);
}
await writeFile("../web/ui/THIRD-PARTY-NOTICES.txt", notices.join("\n\n"));
await copyFile("node_modules/@fontsource-variable/inter/files/inter-latin-wght-normal.woff2", "../web/ui/inter-latin.woff2");
await copyFile("node_modules/@fontsource-variable/inter/LICENSE", "../web/ui/Inter-LICENSE");
