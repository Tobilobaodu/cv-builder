import type { Context, TextMapGetter, TextMapPropagator, TextMapSetter } from "@opentelemetry/api";
/**
 * Same as the `W3CTraceContextPropagator` from `@opentelemetry/core`, but with
 * a workaround for RegExp issue in Edge.
 */
export declare class W3CTraceContextPropagator implements TextMapPropagator {
    fields(): string[];
    inject(context: Context, carrier: any, setter: TextMapSetter): void;
    extract(context: Context, carrier: any, getter: TextMapGetter): Context;
}
