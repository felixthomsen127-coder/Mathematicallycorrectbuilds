from meta_build_comparison import compare_optimizer_build_to_ugg

result = compare_optimizer_build_to_ugg(
    champion='aatrox',
    optimizer_item_names=['Sundered Sky', 'Black Cleaver'],
    role='jungle',
)

print(f'Available: {result.get("available")}')
print(f'Source: {result.get("source")}')
print(f'Reason: {result.get("reason")}')
print(f'Warnings: {result.get("warnings")}')
print(f'Meta Builds Count: {len(result.get("meta_builds", []))}')
print(f'Fallback Used: {result.get("fallback_used")}')
print(f'Cache Used: {result.get("cache_used")}')
print(f'Live Fetch Failed: {result.get("live_fetch_failed")}')
