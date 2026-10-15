export function GET() {
  return Response.json({ purpose: "pydantic-next-spike", ready: true, node: process.version });
}
