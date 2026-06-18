let
    RootFolder = "C:\Users\Gamer\Music\scm\supply-chain-optimization\power-bi\dataset",
    Source = Csv.Document(File.Contents(RootFolder & "\simulation.csv"), [Delimiter=",", Encoding=65001, QuoteStyle=QuoteStyle.Csv])
in
    Source
