// Frontend validation types for enhanced startup wizard

export type ClientValidationRule = {
  field: string;
  type: string;
  required: boolean;
  min_length?: number;
  max_length?: number;
  pattern?: string | null;
  min_value?: number | null;
  max_value?: number | null;
  custom_rules?: string[];
}

export type FieldValidationState = {
  isValid: boolean;
  error: string | null;
  isWarning: boolean;
  warning: string | null;
  isValidating: boolean;
}

export type ValidationState = {
  fields: Record<string, FieldValidationState>;
  overallSuccess: boolean;
  errors: ValidationError[];
  warnings: ValidationError[];
  isValidating: boolean;
  duration?: number;
  nextSteps?: string[];
  estimatedTime?: string;
}

export type ValidationError = {
  field: string;
  message: string;
  severity: 'error' | 'warning' | 'info';
  suggestions: string[];
  code: string;
  isWarning?: boolean;
}

export type VerifyConfigurationRequest = {
  config: Record<string, any>;
  partial?: boolean;
  skip_service_checks?: boolean;
}

export type VerifyConfigurationResponse = {
  success: boolean;
  result: {
    success: boolean;
    errors: ValidationError[];
    warnings: ValidationError[];
    client_side_rules: Record<string, ClientValidationRule>;
    timestamp: string;
    duration_ms?: number;
  };
  next_steps: string[];
  estimated_time: string;
  error?: string;
  hint?: string;
}

// Runtime export to ensure Vite doesn't optimize away the module
export const VALIDATION_MODULE_LOADED = true;