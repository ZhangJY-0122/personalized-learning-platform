const fs = require('fs');
const root = __dirname + '/../web/src/';
const main = fs.readFileSync(root + 'main.js', 'utf8');
const practice = fs.readFileSync(root + 'practice.js', 'utf8');
const recs = fs.readFileSync(root + 'recommendations.js', 'utf8');
for (const needle of ['学习路径', 'pathNodeId', '恢复']) {
  if (!main.includes(needle) && !practice.includes(needle) && !recs.includes(needle)) throw new Error(`missing UI contract: ${needle}`);
}
if (!practice.includes('f21-attempt:') || !practice.includes('Idempotency-Key')) throw new Error('practice retry attribution key missing');
console.log('G4 UI offline assertions passed');
