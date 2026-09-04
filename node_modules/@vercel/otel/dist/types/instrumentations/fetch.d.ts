/// <reference types="node" />
/// <reference types="node" />
import type * as http from "node:http";
import type * as https from "node:https";
import type { Attributes, TracerProvider } from "@opentelemetry/api";
import type { Instrumentation, InstrumentationConfig } from "@opentelemetry/instrumentation";
type Http = typeof http;
type Https = typeof https;
/**
 * Configuration for the "fetch" instrumentation.
 *
 * Some of this configuration can be overriden on a per-fetch call basis by
 * using the `opentelemetry` property in the `RequestInit` object (requires Next 14.1.1 or above).
 * This property can include:
 * - `ignore`: boolean - whether to ignore the fetch call from tracing. Overrides
 *   `ignoreUrls`.
 * - `propagateContext: boolean`: overrides `propagateContextUrls` for this call.
 * - `spanName: string`: overrides the computed span name for this call.
 * - `attributes: Attributes`: overrides the computed attributes for this call.
 */
export interface FetchInstrumentationConfig extends InstrumentationConfig {
    /**
     * A set of URL matchers (string prefix or regex) that should be ignored from tracing.
     * By default all URLs are traced.
     * Can be overriden by the `opentelemetry.ignore` property in the `RequestInit` object.
     *
     * Example: `fetch: { ignoreUrls: [/example.com/] }`.
     */
    ignoreUrls?: (string | RegExp)[];
    /**
     * A set of URL matchers (string prefix or regex) for which the tracing context
     * should be propagated (see [`propagators`](Configuration#propagators)).
     * By default the context is propagated _only_ for the
     * [deployment URLs](https://vercel.com/docs/deployments/generated-urls), all
     * other URLs should be enabled explicitly.
     * Can be overriden by the `opentelemetry.propagateContext` property in the `RequestInit` object.
     *
     * Example: `fetch: { propagateContextUrls: [ /my.api/ ] }`.
     */
    propagateContextUrls?: (string | RegExp)[];
    /**
     * A set of URL matchers (string prefix or regex) for which the tracing context
     * should not be propagated (see [`propagators`](Configuration#propagators)). This allows you to exclude a
     * subset of URLs allowed by the [`propagateContextUrls`](FetchInstrumentationConfig#propagateContextUrls).
     * Can be overriden by the `opentelemetry.propagateContext` property in the `RequestInit` object.
     */
    dontPropagateContextUrls?: (string | RegExp)[];
    /**
     * A string for the "resource.name" attribute that can include attribute expressions in `{}`.
     * Can be overriden by the `opentelemetry.attributes` property in the `RequestInit` object.
     *
     * Example: `fetch: { resourceNameTemplate: "{http.host}" }`.
     */
    resourceNameTemplate?: string;
    /**
     * A map of attributes that should be created from the request headers. The keys of the map are
     * attribute names and the values are request header names. If a resonse header doesn't exist, no
     * attribute will be created for it.
     *
     * Example: `fetch: { attributesFromRequestHeaders: { "attr1": "X-Attr" } }`
     */
    attributesFromRequestHeaders?: Record<string, string>;
    /**
     * A map of attributes that should be created from the response headers. The keys of the map are
     * attribute names and the values are response header names. If a resonse header doesn't exist, no
     * attribute will be created for it.
     *
     * Example: `fetch: { attributesFromResponseHeaders: { "attr1": "X-Attr" } }`
     */
    attributesFromResponseHeaders?: Record<string, string>;
}
declare global {
    interface RequestInit {
        opentelemetry?: {
            ignore?: boolean;
            propagateContext?: boolean;
            spanName?: string;
            attributes?: Attributes;
        };
    }
}
export declare class FetchInstrumentation implements Instrumentation {
    instrumentationName: string;
    instrumentationVersion: string;
    constructor(config?: FetchInstrumentationConfig);
    getConfig(): FetchInstrumentationConfig;
    setConfig(): void;
    setTracerProvider(tracerProvider: TracerProvider): void;
    setMeterProvider(): void;
    private shouldIgnore;
    private shouldPropagate;
    private startSpan;
    instrumentHttp(httpModule: Http | Https, protocolFromModule: "https:" | "http:"): void;
    instrumentFetch(): void;
    enable(): void;
    disable(): void;
}
export {};
