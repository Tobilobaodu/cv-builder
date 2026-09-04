import { fileURLToPath } from 'url';
import { createRequire as topLevelCreateRequire } from 'module';
import _nPath from 'path'
const require = topLevelCreateRequire(import.meta.url);
const __filename = fileURLToPath(import.meta.url);
const __dirname = _nPath.dirname(__filename);
import{a}from"./chunk-NWCAEW5Y.js";import{c,g as d,h as s,i as u}from"./chunk-5MU5Z6L3.js";var g=c(e=>{Object.defineProperty(e,"__esModule",{value:!0});e.getMachineId=void 0;var o=a(),l=(u(),d(s));async function f(){try{let i=(await(0,o.execAsync)('ioreg -rd1 -c "IOPlatformExpertDevice"')).stdout.split(`
`).find(r=>r.includes("IOPlatformUUID"));if(!i)return;let n=i.split('" = "');if(n.length===2)return n[1].slice(0,-1)}catch(t){l.diag.debug(`error reading machine id: ${t}`)}}e.getMachineId=f});export default g();
//# sourceMappingURL=getMachineId-darwin-LT47KAXP.js.map
