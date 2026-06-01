// 模拟 Chat.tsx 中最新版的 preprocessMarkdown
function preprocessMarkdown(text) {
  if (!text) return text;

  let result = text;

  // ===== 1. 修复标题标记后缺少空格的问题 =====
  // 匹配行首或换行后的 # 序列（1-6个），后面紧跟非空格、非#的字符
  result = result.replace(/(^|\n)(#{1,6})(?!\s)(?!#)/gm, '$1$2 ');

  // ===== 2. 修复列表标记后缺少空格的问题 =====
  // 行首无序列表：-/*/+ 后跟非空白、非-、非*的字符（避免匹配 --- 或 **）
  result = result.replace(/^(\s*[-*+])(?![\s\-*])/gm, '$1 ');
  result = result.replace(/^(\s*\d+\.)(?=\S)/gm, '$1 ');
  // 行内列表（不在行首）："服务未启动-配置冲突" → "服务未启动\n- 配置冲突"
  result = result.replace(/([\u4e00-\u9fff\u3002\uff1f\uff01\u3001])\s*-([\u4e00-\u9fff])/g, '$1\n- $2');

  // ===== 3. 修复分隔线 --- 前后换行问题 =====
  result = result.replace(/([^\n])\n---\n([^\n])/g, '$1\n\n---\n\n$2');

  // ===== 4. 修复引用标记后缺少空格的问题 =====
  result = result.replace(/^>([^>\s])/gm, '> $1');

  // ===== 5. 修复连续标题之间缺少换行和空格的问题 =====
  // 匹配：非换行、非#字符后跟完整的 # 序列（1-6个），后面跟数字
  // 处理 "关键要点###1.报错" → "关键要点\n\n### 1.报错"
  // 使用 [^\n#] 避免把 ## 拆成 #\n\n#
  // 注意：步骤1已经处理了行首的 ###后跟非空格，所以这里只处理行中的情况
  result = result.replace(/([^\n#])(#{1,6})(\d)/g, '$1\n\n$2 $3');

  return result;
}

// ===== 测试用例 =====

// 测试 1: 用户实际遇到的 LLM 输出
const test1 = '以下是文档 **mysql连接命令，报错解决.txt** 的总结：\n\n##核心主题解决 MySQL连接时出现的 **"Connection reset / Communications link failure"**报错问题。\n\n---\n\n##关键要点###1.报错原因MySQL服务连接中断，可能由以下原因引起：\n- MySQL服务未启动-配置冲突-驱动不兼容###2.基础排查方法-在 Windows中运行 services.msc';

console.log('========== 测试 1: 用户实际 LLM 输出 ==========');
console.log('--- 原始 ---');
console.log(test1);
console.log();
console.log('--- 处理后 ---');
const r1 = preprocessMarkdown(test1);
console.log(r1);
console.log();
console.log('--- 原始 (JSON) ---');
console.log(JSON.stringify(test1));
console.log();
console.log('--- 处理后 (JSON) ---');
console.log(JSON.stringify(r1));
console.log();

// 验证关键点
console.log('--- 验证 ---');
console.log('**mysql连接命令** 保留（未加空格）:', r1.includes('**mysql连接命令，报错解决.txt**') ? '✅ 正确' : '❌ 错误');
console.log('##核心主题 → ## 核心主题:', r1.includes('## 核心主题') ? '✅ 正确' : '❌ 错误');
console.log('## 未被拆成 #\\n\\n#:', r1.includes('#\n\n# 核心') === false ? '✅ 正确' : '❌ 错误');
console.log('--- 保留:', r1.includes('---') ? '✅ 正确' : '❌ 错误');
console.log('###1.报错原因 → ### 1.报错原因:', r1.includes('### 1.报错原因') ? '✅ 正确' : '❌ 错误');
console.log('-MySQL → - MySQL:', r1.includes('- MySQL') ? '✅ 正确' : '❌ 错误');
console.log('未启动-配置冲突 → 未启动\\n- 配置冲突:', r1.includes('未启动\n- 配置冲突') ? '✅ 正确' : '❌ 错误');
console.log('###2.基础排查方法 → ### 2.基础排查方法:', r1.includes('### 2.基础排查方法') ? '✅ 正确' : '❌ 错误');
console.log();

// 测试 2: 正常 Markdown（不应被破坏）
const test2 = '# 一级标题\n\n## 二级标题\n\n**加粗文本**\n\n- 列表项1\n- 列表项2\n\n> 引用文本\n\n---\n\n普通文本';
console.log('========== 测试 2: 正常 Markdown（不应被破坏） ==========');
console.log('--- 原始 ---');
console.log(test2);
console.log();
console.log('--- 处理后 ---');
const r2 = preprocessMarkdown(test2);
console.log(r2);
console.log();
console.log('--- 验证 ---');
console.log('# 一级标题 保留:', r2.includes('# 一级标题') ? '✅' : '❌');
console.log('## 二级标题 保留:', r2.includes('## 二级标题') ? '✅' : '❌');
console.log('**加粗文本** 保留:', r2.includes('**加粗文本**') ? '✅' : '❌');
console.log('- 列表项1 保留:', r2.includes('- 列表项1') ? '✅' : '❌');
console.log('> 引用文本 保留:', r2.includes('> 引用文本') ? '✅' : '❌');
console.log('--- 保留:', r2.includes('---') ? '✅' : '❌');
console.log();

// 测试 3: 边界情况 - 多个连续标题
const test3 = '前面文字\n\n##标题1\n\n###标题2\n\n正文内容';
console.log('========== 测试 3: 多个连续标题 ==========');
console.log('--- 原始 ---');
console.log(test3);
console.log();
console.log('--- 处理后 ---');
const r3 = preprocessMarkdown(test3);
console.log(r3);
console.log();
console.log('--- 验证 ---');
console.log('## 标题1:', r3.includes('## 标题1') ? '✅' : '❌');
console.log('### 标题2:', r3.includes('### 标题2') ? '✅' : '❌');
console.log('## 未被拆:', r3.includes('#\n\n# 标题') === false ? '✅ 正确' : '❌ 错误');
console.log();

// 测试 4: 中文加粗
const test4 = '这是 **核心主题** 和 **关键要点** 的测试';
console.log('========== 测试 4: 中文加粗 ==========');
console.log('--- 原始 ---');
console.log(test4);
console.log();
console.log('--- 处理后 ---');
const r4 = preprocessMarkdown(test4);
console.log(r4);
console.log();
console.log('--- 验证 ---');
console.log('**核心主题** 保留:', r4.includes('**核心主题**') ? '✅' : '❌');
console.log('**关键要点** 保留:', r4.includes('**关键要点**') ? '✅' : '❌');
console.log('未添加多余空格:', r4 === test4 ? '✅ 完全一致' : '❌ 有变化');
console.log();

// 测试 5: 行首 ** 不应被当作列表
const test5 = '**重要说明**：这是一个加粗文本放在行首的情况\n\n**另一个加粗** 后面跟文字';
console.log('========== 测试 5: 行首 ** 不应被当作列表 ==========');
console.log('--- 原始 ---');
console.log(test5);
console.log();
console.log('--- 处理后 ---');
const r5 = preprocessMarkdown(test5);
console.log(r5);
console.log();
console.log('--- 验证 ---');
console.log('**重要说明** 保留:', r5.includes('**重要说明**') ? '✅' : '❌');
console.log('**另一个加粗** 保留:', r5.includes('**另一个加粗**') ? '✅' : '❌');
console.log();

// 测试 6: 行内 ### 后跟数字（如 "要点###1.内容"）
const test6 = '前面文字###1.第一点内容\n\n中间文字###2.第二点内容';
console.log('========== 测试 6: 行内 ### 后跟数字 ==========');
console.log('--- 原始 ---');
console.log(test6);
console.log();
console.log('--- 处理后 ---');
const r6 = preprocessMarkdown(test6);
console.log(r6);
console.log();
console.log('--- 验证 ---');
console.log('### 1.第一点内容:', r6.includes('### 1.第一点内容') ? '✅' : '❌');
console.log('### 2.第二点内容:', r6.includes('### 2.第二点内容') ? '✅' : '❌');
