#!/usr/bin/env node
// Generates src/lib/api-types.ts (zod schemas and types) from the backend's OpenAPI contract.
//
//   npm run gen:api                         from the committed docs/contracts/openapi.json
//   npm run gen:api -- http://127.0.0.1:8000/openapi.json   from a running API
//   npm run gen:api -- --check              exit 1 when the committed api-types.ts is stale
//
// Response schemas are lenient on purpose (docs/contracts/CHANGES.md item 5): objects keep unknown fields and
// enums accept unknown values, so an additive backend change never breaks a parse. Schemas reachable only from
// request bodies are strict and keep their numeric and length limits, so bad input is caught before it is sent.
import { createHash } from "node:crypto";
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const DEFAULT_SOURCE = resolve(here, "../../../docs/contracts/openapi.json");
const OUTPUT = resolve(here, "../src/lib/api-types.ts");

const args = process.argv.slice(2);
const check = args.includes("--check");
const source = args.find((a) => !a.startsWith("--")) ?? DEFAULT_SOURCE;

async function load(src) {
  if (/^https?:\/\//.test(src)) {
    const res = await fetch(src, { signal: AbortSignal.timeout(10_000) });
    if (!res.ok) throw new Error(`GET ${src} answered ${res.status}`);
    return await res.text();
  }
  return readFileSync(src, "utf8");
}

const refName = (ref) => ref.split("/").at(-1);
const pascal = (s) => s.replace(/(^|_)([a-z0-9])/g, (_, __, c) => c.toUpperCase());

function refsIn(schema, out = new Set()) {
  if (Array.isArray(schema)) schema.forEach((s) => refsIn(s, out));
  else if (schema && typeof schema === "object") {
    if (typeof schema.$ref === "string") out.add(refName(schema.$ref));
    Object.values(schema).forEach((v) => refsIn(v, out));
  }
  return out;
}

function closure(names, schemas) {
  const seen = new Set();
  const stack = [...names];
  while (stack.length) {
    const n = stack.pop();
    if (seen.has(n)) continue;
    seen.add(n);
    refsIn(schemas[n]).forEach((r) => stack.push(r));
  }
  return seen;
}

function usage(spec) {
  const req = new Set();
  const res = new Set();
  for (const ops of Object.values(spec.paths)) {
    for (const op of Object.values(ops)) {
      for (const c of Object.values(op.requestBody?.content ?? {})) refsIn(c.schema, req);
      for (const r of Object.values(op.responses ?? {})) for (const c of Object.values(r.content ?? {})) refsIn(c.schema, res);
    }
  }
  const schemas = spec.components.schemas;
  const responseSide = closure(res, schemas);
  const requestOnly = new Set([...closure(req, schemas)].filter((n) => !responseSide.has(n)));
  return requestOnly;
}

function topoOrder(schemas) {
  const order = [];
  const state = new Map();
  const visit = (n) => {
    if (state.get(n) === 2) return;
    if (state.get(n) === 1) throw new Error(`schema cycle through ${n}; add z.lazy support to gen-api.mjs`);
    state.set(n, 1);
    [...refsIn(schemas[n])].sort().forEach(visit);
    state.set(n, 2);
    order.push(n);
  };
  Object.keys(schemas).sort().forEach(visit);
  return order;
}

const lit = (v) => JSON.stringify(v);

function emit(schema, strict, ctx) {
  if (schema === undefined || schema === true || Object.keys(schema).length === 0) return "z.unknown()";
  if (schema.$ref) return refName(schema.$ref);
  if (schema.anyOf) {
    const nonNull = schema.anyOf.filter((s) => s.type !== "null");
    const nullable = nonNull.length < schema.anyOf.length;
    const inner = nonNull.length === 1 ? emit(nonNull[0], strict, ctx) : `z.union([${nonNull.map((s) => emit(s, strict, ctx)).join(", ")}])`;
    return nullable ? `${inner}.nullable()` : inner;
  }
  if (Array.isArray(schema.type)) {
    const nonNull = schema.type.filter((t) => t !== "null");
    const inner = nonNull.length === 1 ? emit({ ...schema, type: nonNull[0] }, strict, ctx) : "z.unknown()";
    return nonNull.length < schema.type.length ? `${inner}.nullable()` : inner;
  }
  if (schema.enum) {
    const values = schema.enum.map(lit).join(", ");
    if (ctx?.enumName) ctx.enums.push(`export const ${ctx.enumName} = [${values}] as const;`);
    return strict ? `z.enum([${values}])` : `openEnum([${values}])`;
  }
  if (schema.const !== undefined) return `z.literal(${lit(schema.const)})`;
  switch (schema.type) {
    case "string": {
      if (schema.contentMediaType || schema.format === "binary") return "z.unknown()";
      let s = "z.string()";
      if (strict && schema.minLength !== undefined) s += `.min(${schema.minLength})`;
      if (strict && schema.maxLength !== undefined) s += `.max(${schema.maxLength})`;
      return s;
    }
    case "integer":
    case "number": {
      let s = schema.type === "integer" ? "z.number().int()" : "z.number()";
      if (strict && schema.minimum !== undefined) s += `.min(${schema.minimum})`;
      if (strict && schema.maximum !== undefined) s += `.max(${schema.maximum})`;
      return s;
    }
    case "boolean":
      return "z.boolean()";
    case "null":
      return "z.null()";
    case "array": {
      let s = `z.array(${emit(schema.items, strict)})`;
      if (strict && schema.minItems !== undefined) s += `.min(${schema.minItems})`;
      if (strict && schema.maxItems !== undefined) s += `.max(${schema.maxItems})`;
      return s;
    }
    case "object": {
      if (!schema.properties) {
        const value = schema.additionalProperties && schema.additionalProperties !== true ? emit(schema.additionalProperties, strict) : "z.unknown()";
        return `z.record(z.string(), ${value})`;
      }
      return objectBody(schema, strict, ctx);
    }
    default:
      return "z.unknown()";
  }
}

function objectBody(schema, strict, ctx) {
  const required = new Set(schema.required ?? []);
  const lines = Object.keys(schema.properties)
    .sort()
    .map((key) => {
      const prop = schema.properties[key];
      const enumName = ctx ? `${ctx.name}${pascal(key)}Values` : undefined;
      const sub = enumName ? { ...ctx, enumName } : undefined;
      const value = emit(prop, strict, sub);
      return `  ${/^[A-Za-z_$][\w$]*$/.test(key) ? key : lit(key)}: ${value}${required.has(key) ? "" : ".optional()"},`;
    });
  return `${strict ? "z.object" : "z.looseObject"}({\n${lines.join("\n")}\n})`;
}

export function generate(text) {
  const spec = JSON.parse(text);
  const schemas = spec.components.schemas;
  const requestOnly = usage(spec);
  const sha = createHash("sha256").update(text).digest("hex");
  const out = [
    "// GENERATED by scripts/gen-api.mjs from docs/contracts/openapi.json. Do not edit; run `npm run gen:api`.",
    `// Contract: ${spec.info.title} ${spec.info.version}, sha256 ${sha}`,
    "/* eslint-disable */",
    'import { z } from "zod";',
    "",
    "/** A response enum that also accepts values added later (CHANGES.md item 5: treat unknown values as in progress). */",
    "function openEnum<const T extends readonly [string, ...string[]]>(_values: T) {",
    '  return z.custom<T[number] | (string & {})>((v) => typeof v === "string", { message: "expected a string" });',
    "}",
    "",
  ];
  for (const name of topoOrder(schemas)) {
    const strict = requestOnly.has(name);
    const ctx = { name, enums: [] };
    const body = emit(schemas[name], strict, ctx);
    out.push(...ctx.enums);
    if (schemas[name].description) out.push(`/** ${schemas[name].description.replace(/\*\//g, "*\\/").split("\n").join(" ")} */`);
    out.push(`export const ${name} = ${body};`, `export type ${name} = z.infer<typeof ${name}>;`, "");
  }
  out.push(`export const CONTRACT_VERSION = ${lit(spec.info.version)};`, `export const CONTRACT_SHA256 = ${lit(sha)};`, "");
  return out.join("\n");
}

const isMain = process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url);
if (isMain) {
  const generated = generate(await load(source));
  if (check) {
    let current = "";
    try {
      current = readFileSync(OUTPUT, "utf8");
    } catch {
      // missing counts as stale
    }
    if (current !== generated) {
      console.error("src/lib/api-types.ts is stale: run `npm run gen:api`");
      process.exit(1);
    }
    console.log("src/lib/api-types.ts matches the contract");
  } else {
    writeFileSync(OUTPUT, generated);
    console.log(`wrote ${OUTPUT}`);
  }
}
