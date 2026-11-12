// Construit le site : copie la page et génère public/Atlas-assistant-IA.zip
// à partir du code d'Atlas (le reste du dépôt). Aucune dépendance npm.
import { readFileSync, writeFileSync, readdirSync, statSync } from "node:fs";
import { join, relative, sep } from "node:path";
import { deflateRawSync } from "node:zlib";
import { fileURLToPath } from "node:url";

const here = fileURLToPath(new URL(".", import.meta.url));
const repo = join(here, "..");
const out = join(here, "public", "Atlas-assistant-IA.zip");
const SKIP = new Set([".git", "site", "data", ".venv", "node_modules", "__pycache__", ".vercel", ".env"]);

const CRC_TABLE = new Uint32Array(256).map((_, n) => {
  let c = n;
  for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
  return c >>> 0;
});
const crc32 = (buf) => {
  let c = 0xffffffff;
  for (const b of buf) c = CRC_TABLE[(c ^ b) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
};

function walk(dir) {
  const files = [];
  for (const name of readdirSync(dir).sort()) {
    if (SKIP.has(name) || name.endsWith(".pyc")) continue;
    const full = join(dir, name);
    if (statSync(full).isDirectory()) files.push(...walk(full));
    else files.push(full);
  }
  return files;
}

const locals = [];
const centrals = [];
let offset = 0;
const dosTime = 0, dosDate = (2026 - 1980) << 9 | 11 << 5 | 12;

for (const file of walk(repo)) {
  const rel = relative(repo, file).split(sep).join("/");
  let data = readFileSync(file);
  if (rel.endsWith(".bat")) data = Buffer.from(data.toString("utf8").replace(/\r?\n/g, "\r\n"), "utf8");
  if (rel === "config/mcp_servers.json") data = Buffer.from('{\n  "mcpServers": {}\n}\n');
  const name = Buffer.from("Atlas/" + rel, "utf8");
  const comp = deflateRawSync(data, { level: 9 });
  const crc = crc32(data);
  const mode = rel.endsWith(".sh") ? 0o100755 : 0o100644;

  const local = Buffer.alloc(30);
  local.writeUInt32LE(0x04034b50, 0); local.writeUInt16LE(20, 4); local.writeUInt16LE(0x0800, 6);
  local.writeUInt16LE(8, 8); local.writeUInt16LE(dosTime, 10); local.writeUInt16LE(dosDate, 12);
  local.writeUInt32LE(crc, 14); local.writeUInt32LE(comp.length, 18); local.writeUInt32LE(data.length, 22);
  local.writeUInt16LE(name.length, 26); local.writeUInt16LE(0, 28);
  locals.push(local, name, comp);

  const central = Buffer.alloc(46);
  central.writeUInt32LE(0x02014b50, 0); central.writeUInt16LE(0x0314, 4); central.writeUInt16LE(20, 6);
  central.writeUInt16LE(0x0800, 8); central.writeUInt16LE(8, 10); central.writeUInt16LE(dosTime, 12);
  central.writeUInt16LE(dosDate, 14); central.writeUInt32LE(crc, 16); central.writeUInt32LE(comp.length, 20);
  central.writeUInt32LE(data.length, 24); central.writeUInt16LE(name.length, 28);
  central.writeUInt32LE((mode << 16) >>> 0, 38); central.writeUInt32LE(offset, 42);
  centrals.push(central, name);

  offset += local.length + name.length + comp.length;
}

const cdSize = centrals.reduce((n, b) => n + b.length, 0);
const end = Buffer.alloc(22);
end.writeUInt32LE(0x06054b50, 0);
end.writeUInt16LE(centrals.length / 2, 8); end.writeUInt16LE(centrals.length / 2, 10);
end.writeUInt32LE(cdSize, 12); end.writeUInt32LE(offset, 16);

writeFileSync(out, Buffer.concat([...locals, ...centrals, end]));
console.log(`Archive créée : ${relative(repo, out)} (${centrals.length / 2} fichiers)`);
