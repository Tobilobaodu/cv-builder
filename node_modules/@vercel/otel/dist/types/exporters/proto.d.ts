/**
 * This file is constructed by:
 *
 * 1. Clone https://github.com/open-telemetry/opentelemetry-js.git
 * 2. Run `protos:generate` in `experimental/packages/otlp-proto-exporter-base/`
 * 3. Fork `experimental/packages/otlp-proto-exporter-base/src/generated/root.js` and inline all encode methods. Throw away the rest of the code.
 *
 * The OTLP protocol is very stable, so these steps would only need to be done rarely.
 */
import type { IExportTraceServiceRequest } from "@opentelemetry/otlp-transformer/build/src/trace/internal-types";
import { Writer } from "protobufjs/minimal";
export declare function encodeTraceServiceRequest(message: IExportTraceServiceRequest): Uint8Array;
export declare function ExportTraceServiceRequest_encode(message: IExportTraceServiceRequest, writer: Writer): Writer;
