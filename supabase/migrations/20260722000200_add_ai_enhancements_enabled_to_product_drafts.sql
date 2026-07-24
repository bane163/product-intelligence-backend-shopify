do $$
begin
    if to_regclass('public.product_drafts') is null then
        return;
    end if;

    alter table public.product_drafts
        add column if not exists ai_enhancements_enabled boolean;

    update public.product_drafts
    set ai_enhancements_enabled = true
    where ai_enhancements_enabled is null;

    alter table public.product_drafts
        alter column ai_enhancements_enabled set default true,
        alter column ai_enhancements_enabled set not null;
end
$$;
