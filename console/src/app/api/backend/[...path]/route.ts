import { NextResponse } from "next/server";
import { backendTarget, readJsonBody, requireSameOrigin } from "@/lib/request-policy";
import { backendRequest, configuration, errorResponse, PRIVATE_HEADERS } from "@/lib/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
type Context = { params: Promise<{ path: string[] }> };

// The browser talks only to this route. It forwards allow-listed requests to the pipeline API with the server-held token.
async function handle(request: Request, context: Context) {
  try {
    const { path } = await context.params;
    const target = backendTarget(request.method, path, new URL(request.url).searchParams);
    let body: unknown;
    if (request.method !== "GET") {
      requireSameOrigin(request, configuration().origins);
      body = await readJsonBody(request);
    }
    const data = await backendRequest(target, request.method, body);
    // Rebuild the JSON response; never pass on upstream headers.
    return NextResponse.json(data, { headers: PRIVATE_HEADERS });
  } catch (error) {
    return errorResponse(error);
  }
}

export const GET = handle;
export const POST = handle;
