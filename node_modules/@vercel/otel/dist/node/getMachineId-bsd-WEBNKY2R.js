import { fileURLToPath } from 'url';
import { createRequire as topLevelCreateRequire } from 'module';
import _nPath from 'path'
const require = topLevelCreateRequire(import.meta.url);
const __filename = fileURLToPath(import.meta.url);
const __dirname = _nPath.dirname(__filename);
import{a as d}from"./chunk-NWCAEW5Y.js";import{a as i,c as n,g as c,h as s,i as u}from"./chunk-5MU5Z6L3.js";var h=n(t=>{Object.defineProperty(t,"__esModule",{value:!0});t.getMachineId=void 0;var a=i("fs"),o=d(),r=(u(),c(s));async function g(){try{return(await a.promises.readFile("/etc/hostid",{encoding:"utf8"})).trim()}catch(e){r.diag.debug(`error reading machine id: ${e}`)}try{return(await(0,o.execAsync)("kenv -q smbios.system.uuid")).stdout.trim()}catch(e){r.diag.debug(`error reading machine id: ${e}`)}}t.getMachineId=g});export default h();
//# sourceMappingURL=getMachineId-bsd-WEBNKY2R.js.map
