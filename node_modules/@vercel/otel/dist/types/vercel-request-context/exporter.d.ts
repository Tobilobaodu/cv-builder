import type { ReadableSpan, SpanExporter } from "@opentelemetry/sdk-trace-base";
import { type ExportResult } from "@opentelemetry/core";
export declare class VercelRuntimeSpanExporter implements SpanExporter {
    export(spans: ReadableSpan[], resultCallback: (result: ExportResult) => void): void;
    shutdown(): Promise<void>;
    forceFlush?(): Promise<void>;
}
