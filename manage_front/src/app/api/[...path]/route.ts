import { NextRequest, NextResponse } from "next/server";

const API_URL = process.env.API_URL || "http://localhost:8080";

async function proxyRequest(req: NextRequest, { params }: { params: Promise<{ path: string[] }> }) {
  const { path } = await params;
  const targetPath = path.join("/");
  const url = new URL(`/api/${targetPath}`, API_URL);

  // Forward query string
  req.nextUrl.searchParams.forEach((value, key) => {
    url.searchParams.set(key, value);
  });

  const headers = new Headers();
  const auth = req.headers.get("authorization");
  if (auth) headers.set("authorization", auth);
  const contentType = req.headers.get("content-type");
  if (contentType) headers.set("content-type", contentType);

  // Use arrayBuffer so binary uploads (zip, etc.) are not corrupted by text decoding.
  const body = req.method !== "GET" && req.method !== "HEAD" ? await req.arrayBuffer() : undefined;

  let res: Response;
  try {
    res = await fetch(url.toString(), {
      method: req.method,
      headers,
      body,
    });
  } catch (e) {
    const code = e instanceof Error && "code" in e ? (e as { code?: string }).code : undefined;
    console.error(`[api-proxy] upstream unreachable: ${url.toString()} (${code})`);
    return new NextResponse(
      JSON.stringify({ detail: `后端服务不可达（${code || "unknown"}），请检查 gateway 容器是否运行` }),
      { status: 502, headers: { "content-type": "application/json" } },
    );
  }

  const responseBody = await res.arrayBuffer();
  return new NextResponse(responseBody, {
    status: res.status,
    headers: { "content-type": res.headers.get("content-type") || "application/json" },
  });
}

export const GET = proxyRequest;
export const POST = proxyRequest;
export const PUT = proxyRequest;
export const DELETE = proxyRequest;
