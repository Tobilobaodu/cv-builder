import type { Configuration } from "./types";
export declare class Sdk {
    private configuration;
    private contextManager;
    private tracerProvider;
    private loggerProvider;
    private meterProvider;
    private disableInstrumentations;
    constructor(configuration?: Configuration);
    start(): void;
    shutdown(): Promise<void>;
}
