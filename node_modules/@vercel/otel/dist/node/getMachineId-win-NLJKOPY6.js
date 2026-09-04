import { fileURLToPath } from 'url';
import { createRequire as topLevelCreateRequire } from 'module';
import _nPath from 'path'
const require = topLevelCreateRequire(import.meta.url);
const __filename = fileURLToPath(import.meta.url);
const __dirname = _nPath.dirname(__filename);
import{a as g}from"./chunk-NWCAEW5Y.js";import{a as s,c as a,g as d,h as o,i as u}from"./chunk-5MU5Z6L3.js";var _=a(e=>{Object.defineProperty(e,"__esModule",{value:!0});e.getMachineId=void 0;var n=s("process"),h=g(),y=(u(),d(o));async function E(){let c="QUERY HKEY_LOCAL_MACHINE\\SOFTWARE\\Microsoft\\Cryptography /v MachineGuid",t="%windir%\\System32\\REG.exe";n.arch==="ia32"&&"PROCESSOR_ARCHITEW6432"in n.env&&(t="%windir%\\sysnative\\cmd.exe /c "+t);try{let r=(await(0,h.execAsync)(`${t} ${c}`)).stdout.split("REG_SZ");if(r.length===2)return r[1].trim()}catch(i){y.diag.debug(`error reading machine id: ${i}`)}}e.getMachineId=E});export default _();
//# sourceMappingURL=getMachineId-win-NLJKOPY6.js.map
