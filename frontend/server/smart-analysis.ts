import { z } from "zod";
import { invokeLLM } from "./_core/llm";

export const smartAnalysisInput = z.object({ text: z.string().trim().min(16).max(6000) });

const analysisSchema = z.object({
  classification: z.enum(["INITIAL_SIGNAL", "UPDATE_EVENT", "CLOSED_EVENT", "UNVERIFIED"]),
  asset: z.string().nullable(),
  side: z.enum(["LONG", "SHORT", "UNKNOWN"]),
  entry: z.number().nullable(),
  stopLoss: z.number().nullable(),
  targets: z.array(z.number()).max(8),
  confidence: z.number().min(0).max(1),
  temporalHint: z.enum(["LIVE_REVIEW", "HISTORICAL_CANDIDATE", "REVIEW_REQUIRED"]),
  explanation: z.string().max(500),
  safetyNotice: z.string().max(300),
});

export type SmartAnalysis = z.infer<typeof analysisSchema>;

export function validateSmartAnalysis(value: unknown): SmartAnalysis {
  return analysisSchema.parse(value);
}

export async function analyzeForwardText(text: string): Promise<SmartAnalysis> {
  const response = await invokeLLM({
    model: "gpt-5-mini",
    messages: [
      {
        role: "system",
        content: "You extract structure from a forwarded crypto trading message. You are not a financial advisor. Never recommend buying, selling, leverage, or execution. Return only the requested JSON. Treat timestamps as unverified unless explicit. Preserve uncertainty and choose REVIEW_REQUIRED when fields conflict.",
      },
      { role: "user", content: text },
    ],
    response_format: {
      type: "json_schema",
      json_schema: {
        name: "capitalguard_signal_extraction",
        strict: true,
        schema: {
          type: "object",
          properties: {
            classification: { type: "string", enum: ["INITIAL_SIGNAL", "UPDATE_EVENT", "CLOSED_EVENT", "UNVERIFIED"] },
            asset: { type: ["string", "null"] },
            side: { type: "string", enum: ["LONG", "SHORT", "UNKNOWN"] },
            entry: { type: ["number", "null"] },
            stopLoss: { type: ["number", "null"] },
            targets: { type: "array", items: { type: "number" }, maxItems: 8 },
            confidence: { type: "number", minimum: 0, maximum: 1 },
            temporalHint: { type: "string", enum: ["LIVE_REVIEW", "HISTORICAL_CANDIDATE", "REVIEW_REQUIRED"] },
            explanation: { type: "string", maxLength: 500 },
            safetyNotice: { type: "string", maxLength: 300 },
          },
          required: ["classification", "asset", "side", "entry", "stopLoss", "targets", "confidence", "temporalHint", "explanation", "safetyNotice"],
          additionalProperties: false,
        },
      },
    },
  });
  const content = response.choices[0]?.message?.content;
  if (typeof content !== "string") throw new Error("AI_ANALYSIS_EMPTY_RESPONSE");
  return validateSmartAnalysis(JSON.parse(content));
}
