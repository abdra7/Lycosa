// Errors as the Rust side serializes them (src-tauri/src/error.rs): a
// controller error envelope (ADR-007) is distinct from an unreachable
// controller, as ApiException and ControllerUnreachableException were.

export interface FieldError {
  field: string;
  message: string;
}

export type AppError =
  | { kind: "api"; status: number; code: string; message: string; details: FieldError[] }
  | { kind: "unreachable"; base_url: string; message: string }
  | { kind: "not_signed_in" }
  | { kind: "invalid" | "insecure" | "keychain" | "supervisor" | "internal"; message: string };

export function isAppError(value: unknown): value is AppError {
  return typeof value === "object" && value !== null && typeof (value as { kind?: unknown }).kind === "string";
}

export function isApiError(value: unknown, status?: number): value is Extract<AppError, { kind: "api" }> {
  return isAppError(value) && value.kind === "api" && (status === undefined || value.status === status);
}

export function isUnreachable(value: unknown): value is Extract<AppError, { kind: "unreachable" }> {
  return isAppError(value) && value.kind === "unreachable";
}

/** One human-friendly line, including field errors when present. */
export function friendly(error: unknown): string {
  if (isAppError(error)) {
    switch (error.kind) {
      case "api": {
        if (error.details.length === 0) return error.message;
        const fields = error.details.map((d) => `${d.field}: ${d.message}`).join("; ");
        return `${error.message} (${fields})`;
      }
      case "unreachable":
        return `Could not reach controller at ${error.base_url}`;
      case "not_signed_in":
        return "You are signed out of this controller.";
      default:
        return error.message;
    }
  }
  if (error instanceof Error) return error.message;
  return typeof error === "string" ? error : "Request failed";
}
