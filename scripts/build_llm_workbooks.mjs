import fs from 'node:fs/promises';
import path from 'node:path';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';

// Parent runs the artifact-operation marker before invoking this builder.
const [inputPath, outputPath] = process.argv.slice(2);
if (!inputPath || !outputPath) throw new Error('사용법: node build_llm_workbooks.mjs 입력.json 출력디렉토리');
const bundledRequire = createRequire('C:/Users/myori/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/package.json');
const { Workbook, SpreadsheetFile } = await import(pathToFileURL(bundledRequire.resolve('@oai/artifact-tool')).href);
const { workbooks } = JSON.parse((await fs.readFile(inputPath, 'utf8')).replace(/^\uFEFF/, ''));
if (!Array.isArray(workbooks) || !workbooks.length) throw new Error('workbooks 배열이 필요합니다.');
const outputDir = path.resolve(outputPath);
const qaDir = path.join(outputDir, 'qa');
const checks = [];
const filenames = new Set();
const column = n => {
  let label = '';
  for (; n > 0; n = Math.floor((n - 1) / 26)) label = String.fromCharCode(65 + (n - 1) % 26) + label;
  return label;
};
const literal = value => typeof value === 'string' && value.startsWith('=') ? `'${value}` : value;
// ponytail: approximate glyph widths; parent visually reviews PNGs and splits over-height answers.
const height = (value, width, size = 10) => {
  const capacity = Math.max(1, (width - 2) * 10 / size);
  const lines = String(value ?? '').split(/\r\n|\r|\n/).reduce((sum, line) => {
    const units = [...line].reduce((n, ch) => n + (ch === '\t' ? 4 : ch.codePointAt(0) > 255 ? 2 : 1), 0);
    return sum + Math.max(1, Math.ceil(units / capacity));
  }, 0);
  return Math.max(22, lines * size * 1.5 + 8);
};
const errorToken = /^#(?:REF!|DIV\/0!|VALUE!|N\/A|NAME\?|NUM!|NULL!|SPILL!|CALC!|GETTING_DATA|BUSY!|CONNECT!|BLOCKED!|FIELD!|UNKNOWN!)/;
await fs.mkdir(qaDir, { recursive: true });
for (const [bookIndex, spec] of workbooks.entries()) {
  if (typeof spec.filename !== 'string' || /[<>:"/\\|?*]/.test(spec.filename) || !/\.xlsx$/i.test(spec.filename)
      || filenames.has(spec.filename.toLowerCase())) throw new Error('고유한 XLSX 파일명이 필요합니다.');
  filenames.add(spec.filename.toLowerCase());
  if (!Array.isArray(spec.sheets) || !spec.sheets.length) throw new Error('시트가 필요합니다.');
  const workbook = Workbook.create();
  const entries = spec.sheets.map(data => ({ data, sheet: workbook.worksheets.add(data.name), cappedRows: [] }));
  for (const [index, entry] of entries.entries()) {
    const { data, sheet, cappedRows } = entry;
    const count = data.headers.length;
    if (!count || data.widths.length !== count || data.widths.some(w => !Number.isFinite(w) || w <= 2 || w > 255)
        || data.headers.some(h => typeof h !== 'string' || !h.trim()) || new Set(data.headers.map(h => h.toLowerCase())).size !== count
        || data.rows.some(row => row.length !== count || row.some(v => v !== null && typeof v !== 'string' && !(typeof v === 'number' && Number.isFinite(v))))) {
      throw new Error(`${data.name}: 헤더·열너비·행 값 구성을 확인하세요.`);
    }
    let lastRow = Math.max(5, data.rows.length + 4);
    for (const item of data.formulas ?? []) {
      const match = /^([A-Z]+)([1-9]\d*)$/.exec(item.cell);
      const col = match?.[1].split('').reduce((n, ch) => n * 26 + ch.charCodeAt(0) - 64, 0);
      if (!match || col > count || Number(match[2]) < 5 || Number(match[2]) > 1048576
          || typeof item.formula !== 'string' || !item.formula.startsWith('=')) throw new Error(`${data.name}: 수식 위치 또는 형식 오류`);
      lastRow = Math.max(lastRow, Number(match[2]));
    }
    if (count > 16384 || lastRow > 1048576) throw new Error('Excel 시트 크기를 초과했습니다.');
    const lastCol = column(count);
    entry.range = `A1:${lastCol}${lastRow}`;
    entry.previewRange = `A1:${lastCol}${Math.min(lastRow, 8)}`;
    sheet.showGridLines = false;
    sheet.getRange(entry.range).format = { font: { name: 'Malgun Gothic', size: 10, color: '#172B4D' }, wrapText: true, verticalAlignment: 'top', rowHeight: 22 };
    data.widths.forEach((width, i) => { sheet.getRange(`${column(i + 1)}1:${column(i + 1)}${lastRow}`).format.columnWidth = width; });
    const setHeight = (row, wanted) => {
      sheet.getRange(`A${row}:${lastCol}${row}`).format.rowHeight = Math.min(409, wanted);
      if (wanted > 409) cappedRows.push({ 행: row, 예상높이: wanted, 적용높이: 409 });
    };
    for (const [row, text, size] of [[1, data.title ?? spec.title ?? '', 14], [2, data.note ?? spec.note ?? '', 10]]) {
      if (count > 1) sheet.mergeCells(`A${row}:${lastCol}${row}`);
      sheet.getRange(`A${row}`).values = [[literal(text)]];
      sheet.getRange(`A${row}`).format.font = { name: 'Malgun Gothic', size, bold: row === 1, color: row === 1 ? '#172B4D' : '#526277' };
      setHeight(row, height(text, data.widths.reduce((a, b) => a + b, 0), size));
    }
    sheet.getRange(`A4:${lastCol}4`).values = [data.headers.map(literal)];
    if (data.rows.length) sheet.getRange(`A5:${lastCol}${data.rows.length + 4}`).values = data.rows.map(row => row.map(literal));
    data.headers.forEach((label, c) => {
      if (label.includes('ID') || label.includes('식별값')) sheet.getRange(`${column(c + 1)}5:${column(c + 1)}${lastRow}`).setNumberFormat('@');
    });
    const table = sheet.tables.add(`A4:${lastCol}${lastRow}`, true, `Book${bookIndex + 1}Sheet${index + 1}`);
    table.showFilterButton = true;
    sheet.getRange(`A4:${lastCol}4`).format = { fill: '#17365D', font: { name: 'Malgun Gothic', size: 10, bold: true, color: '#FFFFFF' }, wrapText: true, horizontalAlignment: 'center', verticalAlignment: 'center' };
    setHeight(4, Math.max(30, ...data.headers.map((value, i) => height(value, data.widths[i] - 2))));
    data.rows.forEach((row, i) => setHeight(i + 5, Math.max(...row.map((value, c) => height(value, data.widths[c])))));
    sheet.freezePanes.freezeRows(4);
  }
  // All sheets and source values exist before cross-sheet formulas are applied.
  for (const { data, sheet } of entries) for (const { cell, formula } of data.formulas ?? []) {
    sheet.getRange(cell).formulas = [[formula]];
    sheet.getRange(cell).setNumberFormat('0.00');
  }
  await workbook.recalculate();
  for (const [index, { data, sheet, range, previewRange, cappedRows }] of entries.entries()) {
    const values = sheet.getRange(range).values;
    const errors = [];
    values.forEach((row, r) => row.forEach((value, c) => {
      if (errorToken.test(String(value ?? ''))) errors.push({ 셀: `${column(c + 1)}${r + 1}`, 값: value });
    }));
    const formulas = (data.formulas ?? []).map(({ cell, formula }) => ({ 셀: cell, 수식: formula, 값: sheet.getRange(cell).values[0][0] }));
    const inspected = await workbook.inspect({ kind: 'region', sheetId: data.name, range: previewRange, maxChars: 2400, tableMaxRows: 8, tableMaxCols: 8, tableMaxCellChars: 100 });
    console.log(JSON.stringify({ 파일: spec.filename, 시트: data.name, 검사범위: range, 오류후보: errors, 높이제한행: cappedRows, 핵심영역: inspected }));
    const preview = await workbook.render({ sheetName: data.name, range: previewRange, scale: 1, format: 'png' });
    const png = `${bookIndex + 1}_${index + 1}.png`;
    await fs.writeFile(path.join(qaDir, png), new Uint8Array(await preview.arrayBuffer()));
    checks.push({ 파일: spec.filename, 시트: data.name, 검사범위: range, 값: values, 수식결과: formulas, 오류후보: errors, 높이제한행: cappedRows, 미리보기: png });
  }
  await fs.writeFile(path.join(qaDir, '검증.json'), JSON.stringify(checks, null, 2), 'utf8');
  const output = await SpreadsheetFile.exportXlsx(workbook);
  await output.save(path.join(outputDir, spec.filename));
  console.log(`${spec.filename}: 저장 완료`);
}
if (checks.some(check => check.오류후보.length || check.높이제한행.length)) {
  console.error('검증.json의 오류 후보 또는 높이 제한 행을 확인하세요.');
  process.exitCode = 1;
}
