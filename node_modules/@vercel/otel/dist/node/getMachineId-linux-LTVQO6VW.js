import { fileURLToPath } from 'url';
import { createRequire as topLevelCreateRequire } from 'module';
import _nPath from 'path'
const require = topLevelCreateRequire(import.meta.url);
const __filename = fileURLToPath(import.meta.url);
const __dirname = _nPath.dirname(__filename);
import{a as n,c,g as a,h as d,i as s}from"./chunk-5MU5Z6L3.js";var f=c(e=>{Object.defineProperty(e,"__esModule",{value:!0});e.getMachineId=void 0;var u=n("fs"),o=(s(),a(d));async function h(){let i=["/etc/machine-id","/var/lib/dbus/machine-id"];for(let r of i)try{return(await u.promises.readFile(r,{encoding:"utf8"})).trim()}catch(t){o.diag.debug(`error reading machine id: ${t}`)}}e.getMachineId=h});export default f();
//# sourceMappingURL=getMachineId-linux-LTVQO6VW.js.map
