// Minimal flat config: TypeScript correctness rules plus React hooks safety.
// Run with `npm run lint`; intentionally not wired into CI.
import tseslint from "typescript-eslint";
import reactHooks from "eslint-plugin-react-hooks";

export default tseslint.config(
  { ignores: ["dist"] },
  {
    files: ["src/**/*.{ts,tsx}"],
    extends: [tseslint.configs.recommended],
    plugins: { "react-hooks": reactHooks },
    // Only the two classic hook rules: the v7 compiler-powered rules reject the
    // established load-in-effect pattern used across the payload views.
    rules: {
      "react-hooks/rules-of-hooks": "error",
      "react-hooks/exhaustive-deps": "error",
    },
  },
);
