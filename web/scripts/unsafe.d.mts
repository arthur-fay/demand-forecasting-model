/** Types of unsafe.mjs (plain JavaScript, run by Node without a build). */
export declare const ALLOWED_EXTENSIONS: readonly string[];
export declare const BINARY_EXTENSIONS: readonly string[];
export declare const ALLOWED_HOSTS: readonly string[];
export declare const UNSAFE: readonly (readonly [string, RegExp])[];
export declare function findUnsafe(text: string): string[];
