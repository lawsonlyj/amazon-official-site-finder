import fs from "node:fs/promises";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const args = parseArgs(process.argv.slice(2));
const output = args["output"];
const inputs = Array.isArray(args["sheet"]) ? args["sheet"] : [args["sheet"]].filter(Boolean);
if (!output || inputs.length === 0) {
  throw new Error("Usage: node build_linked_workbook.mjs --sheet Name=path.csv [--sheet Name=path.csv] --output out.xlsx");
}

const workbook = Workbook.create();
for (const spec of inputs) {
  const [sheetName, csvPath] = splitSpec(spec);
  const rows = parseCsv(await fs.readFile(csvPath, "utf8"));
  const sheet = workbook.worksheets.add(sheetName);
  writeSheet(sheet, rows);
}

await fs.mkdir(output.substring(0, output.lastIndexOf("/")) || ".", { recursive: true });
const file = await SpreadsheetFile.exportXlsx(workbook);
await file.save(output);
console.log(JSON.stringify({ output, sheets: inputs.length }, null, 2));

function parseArgs(argv) {
  const out = {};
  for (let i = 0; i < argv.length; i += 1) {
    const key = argv[i];
    if (!key.startsWith("--")) continue;
    const name = key.slice(2);
    const value = argv[i + 1];
    i += 1;
    if (out[name] === undefined) {
      out[name] = value;
    } else if (Array.isArray(out[name])) {
      out[name].push(value);
    } else {
      out[name] = [out[name], value];
    }
  }
  return out;
}

function splitSpec(spec) {
  const idx = spec.indexOf("=");
  if (idx < 1) throw new Error(`Invalid sheet spec: ${spec}`);
  return [safeSheetName(spec.slice(0, idx)), spec.slice(idx + 1)];
}

function safeSheetName(value) {
  return value.replace(/[\\/*?:[\]]/g, " ").slice(0, 31) || "Sheet";
}

function writeSheet(sheet, rows) {
  if (rows.length === 0) {
    sheet.getRange("A1").values = [["No rows"]];
    return;
  }
  const headers = Object.keys(rows[0]);
  const matrix = [headers, ...rows.map((row) => headers.map((header) => cellValue(row[header], header)))];
  sheet.getRange(address(1, 1, matrix.length, headers.length)).values = matrix;

  const linkColumns = headers
    .map((header, index) => ({ header, index }))
    .filter(({ header }) => isLinkColumn(header));
  for (const { header, index } of linkColumns) {
    for (let rowIndex = 0; rowIndex < rows.length; rowIndex += 1) {
      const url = normalizeUrl(rows[rowIndex][header]);
      if (!url) continue;
      const cell = sheet.getRange(address(rowIndex + 2, index + 1, 1, 1));
      cell.formulas = [[`=HYPERLINK("${escapeFormulaString(url)}","${escapeFormulaString(url)}")`]];
      cell.format.font = { color: "#0563C1", underline: "single" };
    }
  }

  const used = sheet.getRange(address(1, 1, matrix.length, headers.length));
  used.format.wrapText = false;
  sheet.getRange(address(1, 1, 1, headers.length)).format.fill = "#1F4E78";
  sheet.getRange(address(1, 1, 1, headers.length)).format.font = { color: "#FFFFFF", bold: true };
  sheet.getRange(address(1, 1, 1, headers.length)).format.horizontalAlignment = "center";
  used.format.borders = { preset: "outside", style: "thin", color: "#D9E2F3" };
  setColumnWidths(sheet, headers);
}

function setColumnWidths(sheet, headers) {
  headers.forEach((header, index) => {
    const width = isLinkColumn(header) ? 260 : Math.min(220, Math.max(90, header.length * 10 + 30));
    sheet.getRange(address(1, index + 1, 1, 1)).format.columnWidthPx = width;
  });
}

function isLinkColumn(header) {
  const normalized = header.toLowerCase();
  return normalized.endsWith("_url") || normalized.includes("url");
}

function cellValue(value, header) {
  if (value === undefined || value === null) return "";
  if (isLinkColumn(header)) return normalizeUrl(value);
  return String(value);
}

function normalizeUrl(value) {
  const raw = String(value || "").trim();
  if (!raw) return "";
  if (raw.startsWith("http://") || raw.startsWith("https://")) return raw;
  if (raw.startsWith("//")) return `https:${raw}`;
  if (/^[A-Za-z0-9.-]+\.[A-Za-z]{2,}(\/.*)?$/.test(raw)) return `https://${raw}`;
  return raw;
}

function escapeFormulaString(value) {
  return String(value).replaceAll('"', '""');
}

function address(row, col, rowCount, colCount) {
  const start = `${columnName(col)}${row}`;
  const end = `${columnName(col + colCount - 1)}${row + rowCount - 1}`;
  return start === end ? start : `${start}:${end}`;
}

function columnName(index) {
  let name = "";
  let n = index;
  while (n > 0) {
    const rem = (n - 1) % 26;
    name = String.fromCharCode(65 + rem) + name;
    n = Math.floor((n - 1) / 26);
  }
  return name;
}

function parseCsv(text) {
  const rows = [];
  let row = [];
  let value = "";
  let inQuotes = false;
  for (let i = 0; i < text.length; i += 1) {
    const ch = text[i];
    const next = text[i + 1];
    if (inQuotes) {
      if (ch === '"' && next === '"') {
        value += '"';
        i += 1;
      } else if (ch === '"') {
        inQuotes = false;
      } else {
        value += ch;
      }
      continue;
    }
    if (ch === '"') {
      inQuotes = true;
    } else if (ch === ",") {
      row.push(value);
      value = "";
    } else if (ch === "\n") {
      row.push(value);
      rows.push(row);
      row = [];
      value = "";
    } else if (ch !== "\r") {
      value += ch;
    }
  }
  if (value || row.length) {
    row.push(value);
    rows.push(row);
  }
  if (rows.length === 0) return [];
  const headers = rows[0];
  return rows.slice(1).filter((cells) => cells.some((cell) => cell !== "")).map((cells) => {
    const out = {};
    headers.forEach((header, index) => {
      out[header] = cells[index] || "";
    });
    return out;
  });
}
