#!/usr/bin/env node
/**
 * F.LUX 最终业务库：只读查询入口（Node 内置 node:sqlite，**零第三方依赖**）。
 *
 * 与旧的 query_legacy.mjs 的区别：本文件适配**新的最终库结构**
 * （_catalog / _coverage / _lineage / _batch + 每表一张事实表），
 * 且必须显式传 --db，不再硬编码任何本机路径。
 *
 * 命令
 *   help
 *   catalog  [--table T]                 指标目录（单位/状态/来源）
 *   status   [--table T]                 各表覆盖、最新日期、保留窗口
 *   stores   [--id ID]                   门店主档
 *   get      --table T [--date D] [--metric M] [--store S] [--scope C] [--limit N]
 *   lineage  --table T [--date D]        原件来源与校验和
 *   audit                                完整性 + 计数 + 未决指标
 *
 * 只读保证：DatabaseSync(path, { readOnly: true }) + PRAGMA query_only=ON
 */
import { DatabaseSync } from 'node:sqlite';
import { existsSync } from 'node:fs';
import { resolve } from 'node:path';

function parseArgs(argv) {
  const out = { _: [] };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a.startsWith('--')) {
      const k = a.slice(2);
      const v = argv[i + 1] && !argv[i + 1].startsWith('--') ? argv[++i] : true;
      out[k] = v;
    } else out._.push(a);
  }
  return out;
}

function openDb(path) {
  if (!path) throw new Error('必须指定 --db <最终业务库路径>');
  const p = resolve(String(path));
  if (!existsSync(p)) throw new Error(`库不存在：${p}`);
  const db = new DatabaseSync(p, { readOnly: true });
  db.exec('PRAGMA query_only=ON');
  return db;
}

function tablesOf(db, meta = false) {
  const sql = meta
    ? "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '\\_%' ESCAPE '\\'"
    : "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE '\\_%' ESCAPE '\\'";
  return db.prepare(sql).all().map((r) => r.name);
}

function q(name) {
  return '"' + String(name).replace(/"/g, '""') + '"';
}

function cmdHelp() {
  return {
    usage: 'node scripts/query.mjs <command> --db <path> [options]',
    commands: {
      catalog: '指标目录：--table',
      status: '覆盖与保留窗口：--table',
      stores: '门店主档：--id',
      get: '取值：--table --date --metric --store --scope --limit',
      lineage: '原件来源：--table --date',
      audit: '数据库审计（只读）',
    },
    notes: [
      '只读：readOnly + query_only，不会写库',
      '未决（pending）指标不返回正式值，只返回状态',
    ],
  };
}

function cmdCatalog(db, args) {
  const rows = args.table
    ? db.prepare('SELECT * FROM _catalog WHERE table_name=? ORDER BY column_name').all(args.table)
    : db.prepare('SELECT * FROM _catalog ORDER BY table_name, column_name').all();
  return { count: rows.length, items: rows };
}

function cmdStatus(db, args) {
  const tables = args.table ? [String(args.table)] : tablesOf(db);
  const out = [];
  for (const t of tables) {
    const cov = db
      .prepare(
        'SELECT business_date, scope_id, status, rows, window_start, window_end, batch_id ' +
          'FROM _coverage WHERE table_name=? ORDER BY business_date DESC LIMIT 5'
      )
      .all(t);
    let last = null;
    let n = 0;
    try {
      const cols = db.prepare(`PRAGMA table_info(${q(t)})`).all().map((c) => c.name);
      n = db.prepare(`SELECT COUNT(*) c FROM ${q(t)}`).get().c;
      if (cols.includes('业务日期')) {
        last = db.prepare(`SELECT MAX("业务日期") d FROM ${q(t)}`).get().d;
      }
    } catch (e) {
      last = null;
    }
    out.push({ table: t, rows: n, latest_business_date: last, coverage_recent: cov });
  }
  const batches = db.prepare('SELECT * FROM _batch ORDER BY committed_at DESC LIMIT 5').all();
  return { tables: out, recent_batches: batches };
}

function cmdStores(db, args) {
  const rows = args.id
    ? db.prepare('SELECT * FROM _store_master WHERE store_id=?').all(String(args.id))
    : db.prepare('SELECT * FROM _store_master ORDER BY store_id').all();
  return { count: rows.length, items: rows };
}

function cmdGet(db, args) {
  const table = String(args.table || '');
  if (!table) throw new Error('get 必须指定 --table');
  const cols = db.prepare(`PRAGMA table_info(${q(table)})`).all().map((c) => c.name);
  if (!cols.length) throw new Error(`表不存在：${table}`);
  const where = [];
  const p = [];
  if (args.date) {
    where.push('"业务日期" = ?');
    p.push(String(args.date));
  }
  if (args.scope) {
    where.push('"_scope_id" = ?');
    p.push(String(args.scope));
  }
  if (args.store && cols.includes('门店标准ID')) {
    where.push('"门店标准ID" = ?');
    p.push(String(args.store));
  }
  const sql =
    `SELECT * FROM ${q(table)}` + (where.length ? ' WHERE ' + where.join(' AND ') : '') +
    ` LIMIT ${Number(args.limit || 20)}`;
  const rows = db.prepare(sql).all(...p);

  // 来源与状态：每个返回行都带覆盖状态 + 原件校验和
  const cat = {};
  for (const c of db.prepare('SELECT column_name, unit, status, formal_source FROM _catalog WHERE table_name=?').all(table)) {
    cat[c.column_name] = c;
  }
  const lin = db
    .prepare('SELECT business_date, scope_id, source_path, source_sha256 FROM _lineage WHERE table_name=?')
    .all(table);
  const out = rows.map((r) => {
    const key = `${r['业务日期']}|${r['_scope_id']}`;
    const hit = lin.find((l) => `${l.business_date}|${l.scope_id}` === key) || {};
    const metrics = {};
    for (const [k, v] of Object.entries(r)) {
      if (k.startsWith('_')) continue;
      const meta = cat[k] || {};
      metrics[k] = {
        value: meta.status === 'pending' ? null : v,
        status: meta.status || 'unknown',
        unit: meta.unit || null,
        formal_source: meta.formal_source || null,
      };
    }
    return {
      business_date: r['业务日期'],
      scope_id: r['_scope_id'],
      batch_id: r['_batch_id'],
      source_sha256: hit.source_sha256 || null,
      source_path: hit.source_path || null,
      metrics,
    };
  });
  if (args.metric) {
    const m = String(args.metric);
    return {
      table, metric: m, count: out.length,
      items: out.map((o) => ({
        business_date: o.business_date, scope_id: o.scope_id,
        value: (o.metrics[m] || {}).value, status: (o.metrics[m] || {}).status,
        unit: (o.metrics[m] || {}).unit, source_sha256: o.source_sha256,
      })),
    };
  }
  return { table, count: out.length, items: out };
}

function cmdLineage(db, args) {
  const table = String(args.table || '');
  if (!table) throw new Error('lineage 必须指定 --table');
  const rows = args.date
    ? db.prepare('SELECT * FROM _lineage WHERE table_name=? AND business_date=? ORDER BY scope_id').all(table, String(args.date))
    : db.prepare('SELECT * FROM _lineage WHERE table_name=? ORDER BY business_date DESC LIMIT 50').all(table);
  return { count: rows.length, items: rows };
}

function cmdAudit(db) {
  const integrity = db.prepare('PRAGMA integrity_check').get();
  const tables = tablesOf(db).map((t) => {
    let n = 0;
    try {
      n = db.prepare(`SELECT COUNT(*) c FROM ${q(t)}`).get().c;
    } catch (e) {
      n = null;
    }
    return { table: t, rows: n };
  });
  const pending = db
    .prepare("SELECT table_name, column_name FROM _catalog WHERE status='pending' ORDER BY table_name")
    .all();
  const null_scope = [];
  for (const t of tablesOf(db)) {
    const cols = db.prepare(`PRAGMA table_info(${q(t)})`).all().map((c) => c.name);
    if (cols.includes('_scope_id')) {
      const c = db.prepare(`SELECT COUNT(*) c FROM ${q(t)} WHERE "_scope_id" IS NULL OR "_scope_id"=''`).get().c;
      if (c) null_scope.push({ table: t, rows_without_scope: c });
    }
  }
  return {
    database: db.prepare('PRAGMA database_list').get()?.file || null,
    integrity_check: integrity.integrity_check,
    read_only: true,
    tables,
    pending_metrics: pending,
    scope_violations: null_scope,
  };
}

export function run(argv) {
  const args = parseArgs(argv);
  const cmd = args._[0] || 'help';
  if (cmd === 'help') return cmdHelp();
  const db = openDb(args.db);
  try {
    switch (cmd) {
      case 'catalog': return cmdCatalog(db, args);
      case 'status': return cmdStatus(db, args);
      case 'stores': return cmdStores(db, args);
      case 'get': return cmdGet(db, args);
      case 'lineage': return cmdLineage(db, args);
      case 'audit': return cmdAudit(db);
      default: return { error: `未知命令：${cmd}`, help: cmdHelp() };
    }
  } finally {
    db.close();
  }
}

if (process.argv[1]) {
  try {
    console.log(JSON.stringify(run(process.argv.slice(2)), null, 1));
  } catch (e) {
    console.log(JSON.stringify({ error: String(e && e.message ? e.message : e) }, null, 1));
    process.exitCode = 1;
  }
}
