import { WebWorkerMLCEngineHandler } from "@mlc-ai/web-llm";

// Runs the real MLCEngine (WebLLM) inside a dedicated worker thread, using
// WebGPU to talk to the machine's actual GPU. This is the same engine used
// by MLC-AI's own in-browser chat demos, not a hand-rolled simulation.
const handler = new WebWorkerMLCEngineHandler();

self.onmessage = (msg) => {
  handler.onmessage(msg);
};
