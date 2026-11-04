/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_FABRIC_RELE?: string;
  readonly VITE_FABRIC_CLAVE?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
