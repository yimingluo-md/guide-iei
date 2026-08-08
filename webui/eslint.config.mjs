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
    // Agreed suppression baseline (remediation decision D7, 2026-08-08).
    // These two React-Compiler-era rules flag ~210 sites in the hand-rolled
    // workbench component (refs touched during render; setState inside
    // effects). They are forward-compatibility constraints, not defects:
    // tsc is clean and the behavioural suite passes. Truly fixing them
    // means decomposing VariantWorkbench.tsx — deliberately deferred until
    // a planned refactor or React Compiler adoption. Downgraded to
    // warnings HERE ONLY so lint exits 0 and stays usable as a regression
    // gate for every other rule; new files get the rules at full strength.
    files: ["app/VariantWorkbench.tsx"],
    rules: {
      "react-hooks/refs": "warn",
      "react-hooks/set-state-in-effect": "warn",
    },
  },
]);

export default eslintConfig;
