import { useState, useCallback, useRef, useEffect } from 'react';
import type { 
  ValidationState, 
  FieldValidationState, 
  ClientValidationRule, 
  VerifyConfigurationRequest,
  VerifyConfigurationResponse,
  ValidationError
} from '../types/validation';

// Debounce utility for real-time validation
function debounce<T extends (...args: any[]) => any>(
  func: T,
  wait: number
): T {
  let timeout: NodeJS.Timeout;
  return ((...args: any[]) => {
    clearTimeout(timeout);
    timeout = setTimeout(() => func(...args), wait);
  }) as T;
}

// Validation utilities
const validateField = (
  value: any,
  rule: ClientValidationRule
): FieldValidationState => {
  const result: FieldValidationState = {
    isValid: true,
    error: null,
    isWarning: false,
    warning: null,
    isValidating: false
  };

  // Required field validation
  if (rule.required && (!value || value === '')) {
    result.isValid = false;
    result.error = 'This field is required';
    return result;
  }

  // Skip other validations if field is empty and not required
  if (!value || value === '') {
    return result;
  }

  // Type validation
  if (rule.type === 'string') {
    const strValue = String(value);
    
    // Length validation
    if (rule.min_length && strValue.length < rule.min_length) {
      result.isValid = false;
      result.error = `Must be at least ${rule.min_length} characters`;
      return result;
    }
    
    if (rule.max_length && strValue.length > rule.max_length) {
      result.isValid = false;
      result.error = `Must be no more than ${rule.max_length} characters`;
      return result;
    }
    
    // Pattern validation
    if (rule.pattern) {
      const regex = new RegExp(rule.pattern);
      if (!regex.test(strValue)) {
        result.isValid = false;
        result.error = 'Invalid format';
        return result;
      }
    }
  }
  
  if (rule.type === 'number') {
    const numValue = Number(value);
    
    // Range validation
    if (rule.min_value !== null && numValue < rule.min_value) {
      result.isValid = false;
      result.error = `Must be at least ${rule.min_value}`;
      return result;
    }
    
    if (rule.max_value !== null && numValue > rule.max_value) {
      result.isValid = false;
      result.error = `Must be no more than ${rule.max_value}`;
      return result;
    }
  }

  // Custom validation rules
  if (rule.custom_rules) {
    for (const customRule of rule.custom_rules) {
      const customResult = applyCustomRule(value, customRule);
      if (!customResult.isValid) {
        result.isValid = false;
        result.error = customResult.error;
        return result;
      }
      if (customResult.isWarning) {
        result.isWarning = true;
        result.warning = customResult.warning;
      }
    }
  }

  return result;
};

// Custom validation rules
const applyCustomRule = (value: any, rule: string): { isValid: boolean; error?: string; isWarning?: boolean; warning?: string } => {
  switch (rule) {
    case 'port_available':
      // This will be checked on the server side
      return { isValid: true };
    
    case 'unique_port':
      // This will be checked on the server side
      return { isValid: true };
    
    case 'path_exists':
      // Basic path format validation
      if (!value || typeof value !== 'string') {
        return { isValid: false, error: 'Invalid path' };
      }
      
      // Check for invalid characters
      if (/["<>|?*]/.test(value)) {
        return { isValid: false, error: 'Path contains invalid characters' };
      }
      
      return { isValid: true };
    
    case 'is_directory':
      // Basic check - real validation happens on server
      if (!value || typeof value !== 'string') {
        return { isValid: false, error: 'Invalid path' };
      }
      return { isValid: true };
    
    case 'has_permissions':
      // Real permission check happens on server
      return { isValid: true };
    
    case 'api_key_format':
      if (!value || typeof value !== 'string') {
        return { isValid: false, error: 'Invalid API key format' };
      }
      
      // Basic API key format - alphanumeric, min 20 chars
      if (!/^[a-zA-Z0-9]{20,}$/.test(value)) {
        return { 
          isValid: false, 
          error: 'API key must be at least 20 alphanumeric characters' 
        };
      }
      
      return { isValid: true };
    
    case 'hostname_format':
      if (!value || typeof value !== 'string') {
        return { isValid: false, error: 'Invalid hostname' };
      }
      
      // Basic hostname validation
      if (!/^[a-zA-Z0-9.-]+$/.test(value)) {
        return { isValid: false, error: 'Invalid hostname format' };
      }
      
      return { isValid: true };
    
    case 'url_format':
      try {
        if (!value || typeof value !== 'string') {
          return { isValid: false, error: 'Invalid URL' };
        }
        
        new URL(value);
        return { isValid: true };
      } catch {
        return { isValid: false, error: 'Invalid URL format' };
      }
    
    case 'executable_path':
      if (!value || typeof value !== 'string') {
        return { isValid: false, error: 'Invalid executable path' };
      }
      
      // Basic executable path check
      if (!/^[a-zA-Z0-9._/\\-]+$/.test(value)) {
        return { isValid: false, error: 'Invalid executable path' };
      }
      
      return { isValid: true };
    
    default:
      return { isValid: true };
  }
};

// Main validation hook
export function useValidation(clientSideRules: Record<string, ClientValidationRule> = {}) {
  const [validationState, setValidationState] = useState<ValidationState>({
    fields: {},
    overallSuccess: false,
    errors: [],
    warnings: [],
    isValidating: false
  });

  const debouncedValidate = useCallback(
    debounce(async (field: string, value: any) => {
      if (!clientSideRules[field]) {
        return;
      }

      setValidationState(prev => ({
        ...prev,
        fields: {
          ...prev.fields,
          [field]: { ...prev.fields[field], isValidating: true }
        },
        isValidating: true
      }));

      const validationResult = validateField(value, clientSideRules[field]);

      setValidationState(prev => ({
        ...prev,
        fields: {
          ...prev.fields,
          [field]: validationResult
        },
        isValidating: false
      }));
    }, 300),
    [clientSideRules]
  );

  const validateField = useCallback((field: string, value: any) => {
    if (!clientSideRules[field]) {
      return;
    }

    const validationResult = validateField(value, clientSideRules[field]);
    
    setValidationState(prev => ({
      ...prev,
      fields: {
        ...prev.fields,
        [field]: validationResult
      }
    }));
  }, [clientSideRules]);

  const validateConfiguration = useCallback(async (config: Record<string, any>, options: {
    partial?: boolean;
    skip_service_checks?: boolean;
  } = {}) => {
    setValidationState(prev => ({
      ...prev,
      isValidating: true
    }));

    try {
      const response = await fetch('/api/setup/verify', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          config,
          ...options
        } as VerifyConfigurationRequest)
      });

      const data: VerifyConfigurationResponse = await response.json();

      if (data.success && data.result) {
        setValidationState({
          fields: {},
          overallSuccess: data.result.success,
          errors: data.result.errors || [],
          warnings: data.result.warnings || [],
          isValidating: false,
          duration: data.result.duration_ms,
          nextSteps: data.next_steps,
          estimatedTime: data.estimated_time
        });

        // Update client-side rules from server response
        if (data.result.client_side_rules) {
          // This would be used to update the rules in the parent component
          return {
            success: true,
            clientSideRules: data.result.client_side_rules,
            ...data.result
          };
        }
      } else {
        setValidationState(prev => ({
          ...prev,
          isValidating: false,
          errors: prev.errors.length > 0 ? prev.errors : [{
            field: 'validation',
            message: data.error || 'Validation failed',
            severity: 'error',
            suggestions: data.hint ? [data.hint] : [],
            code: 'VALIDATION_FAILED'
          }],
          overallSuccess: false
        }));
      }

      return data;
    } catch (error) {
      setValidationState(prev => ({
        ...prev,
        isValidating: false,
        errors: [{
          field: 'validation',
          message: error instanceof Error ? error.message : 'Validation failed',
          severity: 'error',
          suggestions: ['Check network connection'],
          code: 'VALIDATION_ERROR'
        }],
        overallSuccess: false
      }));

      return {
        success: false,
        error: error instanceof Error ? error.message : 'Validation failed'
      };
    }
  }, []);

  const clearValidation = useCallback(() => {
    setValidationState({
      fields: {},
      overallSuccess: false,
      errors: [],
      warnings: [],
      isValidating: false
    });
  }, []);

  const getFieldValidation = useCallback((field: string): FieldValidationState => {
    return validationState.fields[field] || {
      isValid: true,
      error: null,
      isWarning: false,
      warning: null,
      isValidating: false
    };
  }, [validationState.fields]);

  const hasErrors = useCallback((field?: string): boolean => {
    if (field) {
      const fieldValidation = getFieldValidation(field);
      return !fieldValidation.isValid || !!fieldValidation.error;
    }
    return validationState.errors.length > 0 || validationState.warnings.some(w => !w.isWarning);
  }, [validationState.errors, validationState.warnings, getFieldValidation]);

  const canProceed = useCallback((fields?: string[]): boolean => {
    const fieldsToCheck = fields || Object.keys(clientSideRules);
    
    for (const fieldName of fieldsToCheck) {
      const validation = getFieldValidation(fieldName);
      if (!validation.isValid || validation.error) {
        return false;
      }
    }
    
    return true;
  }, [clientSideRules, getFieldValidation]);

  return {
    validationState,
    validateField,
    debouncedValidate,
    validateConfiguration,
    clearValidation,
    getFieldValidation,
    hasErrors,
    canProceed
  };
}