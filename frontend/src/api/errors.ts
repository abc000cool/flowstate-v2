/** The client's HTTP error, in its own module so the in-browser demo backend
 * (`mocks/mockApi`) can raise the same error the API would without importing
 * the client that imports it. `api/client` re-exports it: import it from
 * there everywhere else. */

/** An HTTP error in the server's own words. `status` is the HTTP status, or 0
 * for an error the client raised before any request left it. */
export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}
