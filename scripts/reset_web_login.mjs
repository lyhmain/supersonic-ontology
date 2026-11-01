#!/usr/bin/env node
/**
 * 修复 SuperSonic 种子账号「password encrypt error」无法网页登录的问题。
 *
 * 背景(详见 docs/KNOWN_ISSUES.md):
 *   网页登录会把密码做 AES-ECB(密钥 'supersonic@2024',CryptoJS 15 字节特例)后提交;
 *   后端收到后「再加密一次」与库中值比对。网页注册的账号存的是同构二次加密值,能登录;
 *   而种子账号(admin/jack/tom/lucy/alice)存的是出厂单次加密值,无论输什么都比对失败。
 *
 * 本脚本按网页协议生成正确的存储值,输出 UPDATE SQL(不直接改库)。
 *
 * 用法:
 *   npm i crypto-js          # 仅需 crypto-js
 *   node scripts/reset_web_login.mjs <用户名> <新密码>
 *
 * 然后把打印的 SQL 在 SuperSonic 元数据库执行(PostgreSQL):
 *   docker exec -i supersonic_postgres psql -U supersonic_user -d postgres < fix.sql
 */
import crypto from "node:crypto";
import CryptoJS from "crypto-js";

const [user, pwd] = process.argv.slice(2);
if (!user || !pwd) {
  console.error("用法: node scripts/reset_web_login.mjs <用户名> <新密码>");
  process.exit(1);
}

// 第一步:复刻网页端 encryptPassword —— AES-ECB('supersonic@2024') base64
const webKey = CryptoJS.enc.Utf8.parse("supersonic@2024");
const X = CryptoJS.AES.encrypt(CryptoJS.enc.Utf8.parse(pwd), webKey,
  { mode: CryptoJS.mode.ECB, padding: CryptoJS.pad.Pkcs7 }).toString();

// 第二步:复刻后端 AESEncryptionUtil.encrypt(x, salt)
//   key = PBKDF2-HMAC-SHA256(x, salt, 65536, 256bit); AES-256-CBC; IV 固定; b64(IV+密文)
const salt = crypto.createHash("sha256").update(user, "utf8").digest().subarray(0, 16);
const key = crypto.pbkdf2Sync(Buffer.from(X, "utf8"), salt, 65536, 32, "sha256");
const iv = Buffer.from("supersonic@bicom", "utf8");
const c = crypto.createCipheriv("aes-256-cbc", key, iv);
const stored = Buffer.concat([iv, c.update(Buffer.from(X, "utf8")), c.final()]).toString("base64");

console.log(`-- 用户: ${user}`);
console.log(`-- 网页提交值 X = ${X}`);
console.log(`-- 执行以下 SQL 后,网页用 ${user} / ${pwd} 即可登录:`);
console.log(`UPDATE s2_user SET password='${stored}', salt='${salt.toString("base64")}' WHERE name='${user}';`);
