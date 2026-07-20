#!/usr/bin/env node
/**
 * bswitch-statusline.js
 *
 * bswitch check の結果をステータスライン向けの文字列として stdout に出力する。
 * Claude Code の statusline.js や tmux / starship などから呼び出して使う。
 *
 * 出力例:
 *   🔀 myproject(write)     ... OK: プロファイル名と権限
 *   🔀 キー不一致           ... MISMATCH: 環境変数のキーが期待値と異なる
 *   🔀 NOT_SET              ... NOT_SET: BACKLOG_API_KEY 未設定
 *   (何も出力しない)         ... grants なし、または bswitch 未インストール
 *
 * 前提:
 *   - bswitch がインストールされていること
 *     https://github.com/ice1203/backlog-switcher
 *   - bswitch check が JSON を stderr に出力するバージョンであること
 *
 * 使い方:
 *   node bswitch-statusline.js
 */

'use strict';

const { execSync } = require('child_process');
const { existsSync } = require('fs');
const path = require('path');

const HOME = process.env.HOME || require('os').homedir();

// bswitch バイナリの候補パス（優先順）
const BSWITCH_CANDIDATES = [
  path.join(HOME, '.local/bin/bswitch'),
  '/usr/local/bin/bswitch',
  '/opt/homebrew/bin/bswitch',
];

const findBswitch = () => BSWITCH_CANDIDATES.find(existsSync) || null;

const runCheck = (bswitchPath) => {
  try {
    // bswitch check は JSON を stderr に出力するため 2>&1 でキャプチャ
    return execSync(`${bswitchPath} check 2>&1`, {
      encoding: 'utf8',
      shell: true,
      timeout: 5000,
    }).trim();
  } catch {
    return null;
  }
};

const formatStatus = (results) => {
  return results.map((r) => {
    if (r.status === 'MISMATCH') return 'キー不一致';
    if (r.status === 'NOT_SET')  return 'NOT_SET';
    return `${r.profile}(${r.permission})`;
  });
};

const main = () => {
  const bswitchPath = findBswitch();
  if (!bswitchPath) return;

  const output = runCheck(bswitchPath);
  if (!output) return;

  let results;
  try {
    results = JSON.parse(output);
  } catch {
    return;
  }

  if (!Array.isArray(results) || results.length === 0) return;

  const labels = formatStatus(results);
  process.stdout.write(`🔀 ${labels.join(', ')}\n`);
};

main();
