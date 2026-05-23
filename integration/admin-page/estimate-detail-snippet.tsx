// SNIPPET to add to your existing admin estimate-detail page
// (probably app/admin/orders/[orderId]/page.tsx or similar).
//
// 1. Import at the top:
//
//      import { RegenerateEstimateButtons } from "@/components/RegenerateEstimateButtons";
//
// 2. Somewhere in the JSX of the page (typically right under the current
//    measurement display block), add:
//
//      <RegenerateEstimateButtons
//        estimateId={estimate.id}
//        currentEngine={(estimate.preliminary?.dataSources as { lidar?: string } | undefined)?.lidar}
//        currentConfidence={estimate.preliminary?.confidenceScore as number | undefined}
//      />
//
// That's it. The component handles its own loading state, error display, and
// page refresh after a successful regeneration.
//
// The estimate-detail page already fetches the estimate from your store, so
// the regenerate button just needs the estimate ID. Auth is enforced by the
// admin layout that wraps this page (or by the requireAdminSession() call
// at the top of the page's Server Component).
