import { NextResponse } from "next/server";
import { configuration, errorResponse, PRIVATE_HEADERS } from "@/lib/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

// Used by the container health check: the console is up and has its settings.
export function GET() {
  try {
    configuration();
    return NextResponse.json({ status: "ok", service: "medallion-console" }, { headers: PRIVATE_HEADERS });
  } catch (error) {
    return errorResponse(error);
  }
}
