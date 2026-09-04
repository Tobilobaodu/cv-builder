/// <reference types="node" />
import type { Attributes } from "@opentelemetry/api";
export declare function getDefaultResourceAttributes({ attributes, env, runtime, serviceName, }: {
    attributes?: Attributes;
    env?: NodeJS.ProcessEnv;
    runtime: string;
    serviceName: string;
}): Attributes;
