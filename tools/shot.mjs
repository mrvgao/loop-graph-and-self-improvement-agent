#!/usr/bin/env node
/* 起一个演示（或回放一份日志），无头 Chrome 打开看板，等 stop，断言面板、零控制台错误，截图 shots/<demo>.png。
 *   node tools/shot.mjs a1_verify_retry -- --fast              （真跑，MODEL 取 env，默认 haiku）
 *   node tools/shot.mjs a1_verify_retry --from-log runs/a1_verify_retry/xxx.jsonl   （零成本回放）
 */
// playwright-core 的位置：PLAYWRIGHT_CORE 环境变量 > 本机 teachboard 的 node_modules（Marvin 的机器）
const PW = process.env.PLAYWRIGHT_CORE ?? "/Users/mgao/Documents/Parallight/teachboard/node_modules/playwright-core/index.mjs";
const { chromium } = await import(PW);
import { spawn } from "node:child_process";
import { mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const H = dirname(fileURLToPath(import.meta.url)); const DEMOS = join(H, "..");
const [demo, ...rest] = process.argv.slice(2);
if (!demo) { console.error("usage: node tools/shot.mjs <demo> [--from-log file] [-- demo args]"); process.exit(2); }
const fromLog = rest.includes("--from-log") ? rest[rest.indexOf("--from-log") + 1] : null;
const extra = rest.includes("--") ? rest.slice(rest.indexOf("--") + 1) : [];
const PORT = 8650 + Math.floor(Math.random() * 40);
const env = { ...process.env, MODEL: process.env.MODEL || "claude-haiku-4-5", LIVE_NO_BROWSER: "1", PYTHONUNBUFFERED: "1" };
const args = fromLog ? ["live/bus.py", "--serve", fromLog, "--port", String(PORT), "--hold", "--no-browser", "--speed", "200"]
                     : [`${demo}.py`, "--no-browser", "--hold", "--port", String(PORT), ...extra];
const proc = spawn("python3", args, { cwd: DEMOS, env, stdio: ["pipe", "pipe", "pipe"] });
let out = ""; proc.stdout.on("data", (d) => { out += d; process.stdout.write(d); }); proc.stderr.on("data", (d) => { out += d; process.stderr.write(d); });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const url = `http://127.0.0.1:${PORT}`;
let up = false;
for (let i = 0; i < 60 && !up; i++) { try { const r = await fetch(url + "/api/ping"); up = r.ok; } catch {} if (!up) await sleep(500); }
if (!up) { console.error("❌ 看板 30 秒没起来"); proc.kill(); process.exit(1); }
const browser = await chromium.launch({ channel: "chrome", headless: true });
const page = await browser.newPage({ viewport: { width: 1700, height: 1100 } });
const errs = []; page.on("pageerror", (e) => errs.push("pageerror: " + e)); page.on("console", (m) => { if (m.type() === "error") errs.push(m.text()); });
await page.goto(url + "/", { waitUntil: "domcontentloaded" });
// 等 stop（最多 12 分钟）
let stopped = false;
for (let i = 0; i < 1440 && !stopped; i++) { const h = await (await fetch(url + "/api/history")).json(); stopped = h.some((e) => e.kind === "stop"); if (!stopped) await sleep(500); }
await sleep(1200);
const panels = await page.evaluate(() => [...document.querySelectorAll("section[data-panel]")].filter((s) => !s.hidden).map((s) => s.dataset.panel));
const stopText = await page.evaluate(() => (document.getElementById("stopchip") || {}).textContent || "");
mkdirSync(join(DEMOS, "shots"), { recursive: true });
await page.screenshot({ path: join(DEMOS, "shots", `${demo}.png`), fullPage: true });
console.log(`\n📸 shots/${demo}.png · panels=${panels.join(",")} · ${stopText} · console errors=${errs.length}`);
errs.slice(0, 5).forEach((e) => console.log("   ", e));
await browser.close();
try { await fetch(url + "/api/done", { method: "POST" }); } catch {}
await new Promise((r) => { proc.on("exit", r); setTimeout(() => { proc.kill(); r(); }, 5000); });
process.exit(errs.length ? 1 : (stopped ? 0 : 1));
