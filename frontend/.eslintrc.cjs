/* eslint-env node */
module.exports = {
  root: true,
  env: { browser: true, es2022: true },
  extends: [
    'eslint:recommended',
    'plugin:@typescript-eslint/recommended',
    'plugin:react-hooks/recommended',
  ],
  parser: '@typescript-eslint/parser',
  parserOptions: { ecmaVersion: 'latest', sourceType: 'module' },
  plugins: ['@typescript-eslint', 'react-refresh'],
  settings: { react: { version: '18.3' } },
  rules: {
    'react-refresh/only-export-components': ['warn', { allowConstantExport: true }],
    '@typescript-eslint/no-unused-vars': [
      'error',
      { argsIgnorePattern: '^_', varsIgnorePattern: '^_' },
    ],
    '@typescript-eslint/consistent-type-imports': [
      'error',
      { prefer: 'type-imports', fixStyle: 'separate-type-imports' },
    ],
    'no-console': ['warn', { allow: ['warn', 'error'] }],
  },
  overrides: [
    {
      files: ['vite.config.ts', 'tailwind.config.js', 'postcss.config.js'],
      env: { node: true },
    },
    {
      // Provider + hook in one module is the intended shape for these files;
      // fast refresh still works because the provider is never remounted.
      files: ['src/hooks/*.tsx'],
      rules: { 'react-refresh/only-export-components': 'off' },
    },
  ],
  ignorePatterns: ['dist', 'node_modules', '*.cjs'],
}
