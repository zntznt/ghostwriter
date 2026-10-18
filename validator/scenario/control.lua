-- Ghostwriter validator harness. Loaded as a scenario by validator/server.py;
-- Python calls it over RCON:
--   /sc remote.call("ghostwriter", "validate", "<blueprint string>")
-- and gets back JSON via rcon.print:
--   {import = rc, export = "<string>", builds = {[path] = build}}
-- rc is LuaItemStack.import_stack's result (0 ok, -1 ok with errors, 1 fail).
-- Each build covers one blueprint (path "" or "1/0" inside books):
--   {entities = n, unplaced = {...}, unplaceable = {...}, unrevived = {...},
--    rebuilt = "<string>"}
-- unplaceable: names that can't be placed even alone on the test surface
-- (surface conditions, water, rails); unplaced: the rest, i.e. the blueprint
-- itself collides.

local SURFACE = "ghostwriter"
local MARGIN = 8

local function surface()
  local s = game.surfaces[SURFACE]
  if not s then
    s = game.create_surface(SURFACE, {default_enable_all_autoplace_controls = false, no_enemies_mode = true})
  end
  return s
end

local function clear(s, area)
  for _, e in pairs(s.find_entities_filtered{area = area}) do
    if e.valid and e.type ~= "character" then e.destroy() end
  end
end

local function prepare(s, area)
  local lt, rb = area[1], area[2]
  s.request_to_generate_chunks({0, 0}, math.ceil(math.max(rb[1] - lt[1], rb[2] - lt[2]) / 64) + 1)
  s.force_generate_chunk_requests()
  clear(s, area)
  local tiles = {}
  for x = math.floor(lt[1]), math.ceil(rb[1]) do
    for y = math.floor(lt[2]), math.ceil(rb[2]) do
      tiles[#tiles + 1] = {name = "refined-concrete", position = {x, y}}
    end
  end
  s.set_tiles(tiles)
end

local function bounds(stack)
  local x1, y1, x2, y2 = 0, 0, 0, 0
  for _, e in pairs(stack.get_blueprint_entities() or {}) do
    x1, y1 = math.min(x1, e.position.x), math.min(y1, e.position.y)
    x2, y2 = math.max(x2, e.position.x), math.max(y2, e.position.y)
  end
  for _, t in pairs(stack.get_blueprint_tiles() or {}) do
    x1, y1 = math.min(x1, t.position.x), math.min(y1, t.position.y)
    x2, y2 = math.max(x2, t.position.x), math.max(y2, t.position.y)
  end
  -- The build is centred on {0,0}, so make the area symmetric and generous.
  local r = math.max(-x1, -y1, x2, y2) + MARGIN
  return {{-r, -r}, {r, r}}
end

local function build(stack)
  local s = surface()
  local area = bounds(stack)
  prepare(s, area)
  local out = {entities = #(stack.get_blueprint_entities() or {}), unplaced = {}, unplaceable = {}, unrevived = {}}

  local ghosts = stack.build_blueprint{
    surface = s, force = "player", position = {0, 0},
    build_mode = defines.build_mode.forced, skip_fog_of_war = false,
  }
  local placed = {}
  for _, g in pairs(ghosts) do
    if g.valid and g.name == "entity-ghost" then
      placed[g.ghost_name] = (placed[g.ghost_name] or 0) + 1
    end
  end
  local wanted = {}
  for _, e in pairs(stack.get_blueprint_entities() or {}) do
    wanted[e.name] = (wanted[e.name] or 0) + 1
  end
  local probe = {area[2][1] + 2 * MARGIN, 0}
  for name, n in pairs(wanted) do
    local missing = n - (placed[name] or 0)
    if missing > 0 then
      prepare(s, {{probe[1] - MARGIN, -MARGIN}, {probe[1] + MARGIN, MARGIN}})
      local alone = s.can_place_entity{name = name, position = probe, force = "player",
                                       build_check_type = defines.build_check_type.manual}
      local list = alone and out.unplaced or out.unplaceable
      list[#list + 1] = name .. " x" .. missing
    end
  end

  -- Tile ghosts first so entities that need the tiles can revive.
  for _, g in pairs(ghosts) do
    if g.valid and g.name == "tile-ghost" then g.revive() end
  end
  for _, g in pairs(ghosts) do
    if g.valid and g.name == "entity-ghost" then
      local name = g.ghost_name
      local _, entity = g.revive()
      if not entity then out.unrevived[#out.unrevived + 1] = name end
    end
  end

  local inv = game.create_inventory(1)
  local copy = inv[1]
  copy.set_stack("blueprint")
  copy.create_blueprint{
    surface = s, force = "player", area = area,
    include_entities = true, include_modules = true, include_station_names = true,
    include_trains = true, include_fuel = true,
  }
  if copy.is_blueprint_setup() then out.rebuilt = copy.export_stack() end
  inv.destroy()
  clear(s, area)
  return out
end

local function build_all(stack, path, builds)
  if stack.is_blueprint and stack.is_blueprint_setup() then
    builds[path] = build(stack)
  elseif stack.is_blueprint_book then
    local inv = stack.get_inventory(defines.inventory.item_main)
    for i = 1, #inv do
      if inv[i].valid_for_read then
        -- Children are addressed by their 0-based book index, as in the JSON.
        build_all(inv[i], (path == "" and "" or path .. "/") .. (i - 1), builds)
      end
    end
  end
end

remote.add_interface("ghostwriter", {
  validate = function(text)
    local inv = game.create_inventory(1)
    local stack = inv[1]
    local result = {import = stack.import_stack(text), builds = {}}
    if stack.valid_for_read then
      result.export = stack.export_stack()
      build_all(stack, "", result.builds)
    end
    inv.destroy()
    rcon.print(helpers.table_to_json(result))
  end,
  version = function()
    rcon.print(script.active_mods["base"])
  end,
})
