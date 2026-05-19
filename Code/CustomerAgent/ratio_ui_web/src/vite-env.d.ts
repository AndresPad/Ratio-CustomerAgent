/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_USE_FIXTURES?: string;
  readonly VITE_ENABLE_INVESTIGATION_STREAM?: string;
  readonly VITE_SCHEDULER_CUSTOMER_NAME?: string;
  readonly VITE_SCHEDULER_CRON_MINUTES?: string;
  readonly VITE_SCHEDULER_WINDOWS?: string;
  readonly VITE_SCHEDULER_JOB_NAME?: string;
  readonly VITE_AZURE_SUBSCRIPTION_ID?: string;
  readonly VITE_AZURE_RESOURCE_GROUP?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
