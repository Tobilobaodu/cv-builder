import type { Context, TextMapPropagator } from "@opentelemetry/api";
export declare class VercelRuntimePropagator implements TextMapPropagator {
    fields(): string[];
    inject(): void;
    extract(context: Context): Context;
}
