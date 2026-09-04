import type { ReadableSpan, SpanExporter } from "@opentelemetry/sdk-trace-base";
import type { ExportResult } from "@opentelemetry/core";
import type { OTLPExporterConfig } from "./config";
/**
 * OTLP exporter for the `http/protobuf` protocol. Compatible with the "edge" runtime.
 */
export declare class OTLPHttpProtoTraceExporter implements SpanExporter {
    constructor(config?: OTLPExporterConfig);
    export(spans: ReadableSpan[], resultCallback: (result: ExportResult) => void): void;
    shutdown(): Promise<void>;
    forceFlush(): Promise<void>;
}
