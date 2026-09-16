require "json"
require "yaml"

markdown = File.read(ARGV.fetch(0))
yaml_text = markdown.match(/^```yaml\n(.*?)^```$/m)&.captures&.first
abort("canonical YAML fence not found") if yaml_text.nil?

canonical = YAML.safe_load(yaml_text, aliases: false)
system_global = JSON.parse(File.read(ARGV.fetch(1)))
service = JSON.parse(File.read(ARGV.fetch(2)))
seed = {
  "system_global_root" => system_global.fetch("root"),
  "service" => service.fetch("service")
}

def first_difference(expected, actual, path = "")
  return nil if expected == actual

  if expected.is_a?(Hash) && actual.is_a?(Hash)
    (expected.keys | actual.keys).sort.each do |key|
      child = first_difference(expected[key], actual[key], "#{path}/#{key}")
      return child unless child.nil?
    end
  elsif expected.is_a?(Array) && actual.is_a?(Array)
    [expected.length, actual.length].max.times do |index|
      child = first_difference(expected[index], actual[index], "#{path}/#{index}")
      return child unless child.nil?
    end
  end

  path.empty? ? "/" : path
end

pointer = first_difference(canonical, seed)
abort("Zone X YAML/JSON semantic mismatch at #{pointer}") unless pointer.nil?
