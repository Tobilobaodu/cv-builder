import { fileURLToPath } from 'url';
import { createRequire as topLevelCreateRequire } from 'module';
import _nPath from 'path'
const require = topLevelCreateRequire(import.meta.url);
const __filename = fileURLToPath(import.meta.url);
const __dirname = _nPath.dirname(__filename);
var b=Object.create;var p=Object.defineProperty;var c=Object.getOwnPropertyDescriptor;var d=Object.getOwnPropertyNames;var g=Object.getPrototypeOf,h=Object.prototype.hasOwnProperty;var k=(o=>typeof require<"u"?require:typeof Proxy<"u"?new Proxy(o,{get:(r,e)=>(typeof require<"u"?require:r)[e]}):o)(function(o){if(typeof require<"u")return require.apply(this,arguments);throw Error('Dynamic require of "'+o+'" is not supported')});var i=(o,r)=>()=>(o&&(r=o(o=0)),r);var l=(o,r)=>()=>(r||o((r={exports:{}}).exports,r),r.exports),n=(o,r)=>{for(var e in r)p(o,e,{get:r[e],enumerable:!0})},m=(o,r,e,a)=>{if(r&&typeof r=="object"||typeof r=="function")for(let f of d(r))!h.call(o,f)&&f!==e&&p(o,f,{get:()=>r[f],enumerable:!(a=c(r,f))||a.enumerable});return o},x=(o,r,e)=>(m(o,r,"default"),e&&m(e,r,"default")),q=(o,r,e)=>(e=o!=null?b(g(o)):{},m(r||!o||!o.__esModule?p(e,"default",{value:o,enumerable:!0}):e,o)),s=o=>m(p({},"__esModule",{value:!0}),o);var t={};import*as v from"@opentelemetry/api";var j=i(()=>{x(t,v)});export{k as a,i as b,l as c,n as d,x as e,q as f,s as g,t as h,j as i};
//# sourceMappingURL=chunk-5MU5Z6L3.js.map
