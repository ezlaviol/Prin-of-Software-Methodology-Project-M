from sqlalchemy import text

SEARCH = text("""
    select * from search.search_products(
    :q, :cat, CAST(:max_price AS numeric),
    :lim, :off)""")

FACETS = text("""
    select category, count(*) as n
    from search.product_search
    where fts @@
    websearch_to_tsquery('english', :q)
    group by category order by n desc""")
