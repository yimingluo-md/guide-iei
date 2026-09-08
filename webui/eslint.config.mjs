import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  // Override default ignores of eslint-config-next.
  globalIgnores([
    // Default ignores of eslint-config-next:
    ".next/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
  ]),
  {
    // Agreed suppression baseline (remediation decision D7, 2026-08-08),
    // narrowed 2026-09-07. `react-hooks/set-state-in-effect` flags a handful
    // of setState-inside-effect sites in the hand-rolled workbench component
    // — a forward-compatibility constraint, not a defect: tsc is clean and
    // the behavioural suite passes. Truly fixing them means decomposing
    // VariantWorkbench.tsx, deliberately deferred until a planned refactor
    // or React Compiler adoption. Downgraded to a warning HERE ONLY so lint
    // exits 0 and stays usable as a regression gate for every other rule.
    // `react-hooks/refs` used to be downgraded too: its ~250 hits were one
    // false positive — a prop literally named `ref` on CcreContextPanel made
    // the rule treat every access to the selected row as a ref read. The
    // prop is now `refAllele`, the rule has zero hits and runs at full
    // strength again (review M36).
    files: ["app/VariantWorkbench.tsx"],
    rules: {
      "react-hooks/set-state-in-effect": "warn",
    },
  },
]);

export default eslintConfig;
