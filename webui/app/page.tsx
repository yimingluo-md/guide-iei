import type { Metadata } from "next";
import VariantWorkbench from "./VariantWorkbench";

export const metadata: Metadata = {
  title: "GUIDE-IEI",
  description: "A clinician-developed, locally run, open-source WES/WGS analysis platform for inborn errors of immunity.",
};

export default function Home() {
  return <VariantWorkbench />;
}
