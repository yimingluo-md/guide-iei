import type { Metadata } from "next";
import VariantWorkbench from "./VariantWorkbench";

export const metadata: Metadata = {
  title: "IEI Variant Review",
  description: "Clinical review workbench for VEP-annotated IEI variants.",
  other: { "codex-preview": "development" },
};

export default function Home() {
  return <VariantWorkbench />;
}
